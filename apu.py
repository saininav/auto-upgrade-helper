#!/usr/bin/env python

import argparse
import os
import logging as log
from logging import debug as D
from logging import info as I
from logging import warning as W
from logging import error as E
from logging import critical as C
import re
import sys

for path in os.environ["PATH"].split(':'):
    if os.path.exists(path) and "bitbake" in os.listdir(path):
        sys.path.insert(0, os.path.join(path, "../lib"))
        import bb


def get_build_dir():
    return os.getenv('BUILDDIR')


class Git():
    def __init__(self, dir):
        self.repo_dir = dir

    def _cmd(self, operation):
        os.chdir(self.repo_dir)

        cmd = "git " + operation
        try:
            stdout, stderr = bb.process.run(cmd)
        except bb.process.ExecutionError as ex:
            D("%s returned:\n%s" % (cmd, ex.__str__()))
            return (-1, ex.stdout, ex.stderr)

        return (0, stdout, stderr)

    def mv(self, src, dest):
        return self._cmd("mv " + src + " " + dest)

    def stash(self):
        return self._cmd("stash")

    def commit(self, commit_message):
        return self._cmd("commit -a -s -m \"" + commit_message + "\"")

    def create_patch(self, out_dir):
        return self._cmd("format-patch -M10 -1 -o " + out_dir)

    def reset_hard(self, no_of_patches=0):
        if no_of_patches == 0:
            return self._cmd("reset --hard HEAD")
        else:
            return self._cmd("reset --hard HEAD~" + str(no_of_patches))


class Bitbake():
    def __init__(self, build_dir):
        self.build_dir = build_dir

    def _cmd(self, recipe, options=None, env_var=None):
        cmd = ""
        if env_var is not None:
            cmd += env_var + " "
        cmd += "bitbake "
        if options is not None:
            cmd += options + " "

        os.chdir(self.build_dir)

        try:
            stdout, stderr = bb.process.run(cmd + recipe)
        except bb.process.ExecutionError as ex:
            D("%s returned:\n%s" % (cmd, ex.__str__()))
            return (-1, ex.stdout, ex.stderr)

        return (0, stdout, stderr)

    def env(self, recipe):
        return self._cmd(recipe, "-e")

    def fetch(self, recipe):
        return self._cmd(recipe, "-c fetch")

    def unpack(self, recipe):
        return self._cmd(recipe, "-c unpack")

    def checkpkg(self, recipe):
        return self._cmd(recipe, "-c checkpkg")

    def cleanall(self, recipe):
        return self._cmd(recipe, "-c cleanall")

    def complete(self, recipe, machine):
        return self._cmd(recipe, env_var="MACHINE=" + machine)


class Package(object):
    ERR_NONE = 0
    ERR_FETCH = 1
    ERR_PATCH = 2
    ERR_PATCH_BACKPORTED = 3
    ERR_CONFIGURE = 4
    ERR_COMPILATION = 5
    ERR_LICENSE = 6
    ERR_UPGRADE_NOT_NEEDED = 7
    ERR_UNSUPPORTED_PROTOCOL = 8
    ERR_OTHER = 9

    def __init__(self):
        self.bb = Bitbake(get_build_dir())
        self.apu_dir = get_build_dir() + "/apu"
        if not os.path.exists(self.apu_dir):
            os.mkdir(self.apu_dir)
        self.machines = ["qemux86", "qemux86-64", "qemuarm", "qemumips", "qemuppc"]

        self.error_handler = {
            "do_fetch": self.handle_error_do_fetch,
            "do_patch": self.handle_error_do_patch,
            "do_configure": self.handle_error_do_configure,
            "do_compile": self.handle_error_do_compile
        }

        self.upgrade_steps = [
            #step_function , message to display before executing the function,
            #                error message if function failed
            (self.create_workdir, None, "Unable to create work directory!"),
            (self.get_env, "Fetch package environment ...",
                           "Could not fetch package environment!"),
            (self.stash_uncommited_work, "Save uncommited work ...",
                                         "git stash failed!"),
            (self.get_env, "Fetch package environment ...",
                           "Could not fetch package environment!"),
            (self.get_next_version, None, "Could not get the next version!"),
            (self.check_upgrade_needed, None, "Already at latest version, upgrade not needed!"),
            (self.check_src_uri_protocol, None, "Unsupported SRC_URI protocol!"),
            (self.unpack_package, "Fetch/Unpack original package ...",
                                  "Fetching/Unpacking original package failed!"),
            (self.rename_files, "Renaming recipes ...",
                                "Rename operation failed"),
            (self.get_env, "Fetch package environment ...",
                           "Could not fetch package environment!"),
            (self.cleanall_package, "Clean all ...",
                                    "Clean all failed!"),
            (self.fetch_package, "Fetch new package (old checksums) ...",
                                 "Fetching new package failed! Either it succeeded \
                                 when it should've failed or it failed but no \
                                 checksums were detected in the log."),
            (self.get_env, "Fetch package environment ...",
                           "Could not fetch package environment!"),
            (self.compile_package, None, "Compilation failed for one or more machines!")
        ]

    def get_env(self):
        err, stdout, stderr = self.bb.env(self.pn)
        if err == -1:
            return None

        assignment = re.compile("^([^ \t]*)=(.*)")
        bb_env = dict()
        for line in stdout.split('\n'):
            m = assignment.match(line)
            if m:
                if m.group(1) in bb_env:
                    continue

                bb_env[m.group(1)] = m.group(2).strip("\"")

        if bb_env is None:
            return self.ERR_OTHER

        self.env = bb_env
        return self.ERR_NONE

    def get_next_version(self):
        if self.to_ver is None:
            I(" %s: Checking next available version..." % self.pn)

            if self.env['INHERIT'].find("distrodata") == -1:
                C(" \"distrodata.bbclass\" not inherited. Consider adding the following to your local.conf:\n"
                  "INHERIT =+ \"distrodata\"\n"
                  "require conf/distro/include/recipe_color.inc\n"
                  "require conf/distro/include/distro_alias.inc\n"
                  "require conf/distro/include/maintainers.inc\n"
                  "require conf/distro/include/upstream_tracking.inc\n")
                exit(1)

            err = self.bb.checkpkg(self.pn)
            if err == -1:
                return self.ERR_OTHER

            csv = open(get_build_dir() + "/tmp/log/checkpkg.csv", "r")
            line = csv.readlines()[1]
            self.to_ver = line.split()[2]
            m = re.match(".*<(.*)@(.*)>.*", line)
            if m is not None:
                self.maintainer = m.group(1) + "@" + m.group(2)
            csv.close()

        if self.to_ver is None or self.to_ver == "N/A" or self.to_ver == "INVALID":
            return self.ERR_OTHER

        return self.ERR_NONE

    def check_upgrade_needed(self):
        if self.to_ver == self.env['PV']:
            return self.ERR_UPGRADE_NOT_NEEDED

        I(" %s: Upgrading to version %s ..." % (self.pn, self.to_ver))

        # start to construct the commit message
        self.commit_msg = self.pn + ": upgrade to " + self.to_ver

        return self.ERR_NONE

    def check_src_uri_protocol(self):
        if self.env['SRC_URI'].find("ftp://") != -1 or  \
                self.env['SRC_URI'].find("http://") != -1 or \
                self.env['SRC_URI'].find("https://") != -1:
            return self.ERR_NONE

        return self.ERR_UNSUPPORTED_PROTOCOL

    def rename_files(self):
        for path in os.listdir(self.recipe_dir):
            if path.find(self.env['PN'] + '-' + self.env['PKGV']) != -1 or \
                    path.find(self.env['PN'] + '_' + self.env['PKGV']) != -1:
                new_path = re.sub(self.env['PKGV'], self.to_ver, path)
                if self.git.mv(path, new_path) == (-1,):
                    return self.ERR_OTHER

        # since renaming was successful, save the old environment so it doesn't
        # get overwritten. It will be needed for license file diffs, etc.
        self.old_env = self.env

        return self.ERR_NONE

    def create_diff_file(self, file, old_md5, new_md5):
        old_file = os.path.join(self.old_env['S'], file)
        new_file = os.path.join(self.env['S'], file)
        cmd = "diff -Nup " + old_file + " " + new_file + " > " + \
              os.path.join(self.workdir, file + ".diff")

        try:
            stdout, stderr = bb.process.run(cmd)
        except bb.process.ExecutionError:
            pass

        with open(os.path.join(self.workdir, "license_checksums.txt"), "w+") as f:
            f.write("old checksum = %s\n" % old_md5)
            f.write("new_checksum = %s\n" % new_md5)

        return 0

    def handle_error_do_fetch(self, fetch_log):
        md5sum_line = None
        sha256sum_line = None

        with open(os.path.realpath(fetch_log)) as log:
            for line in log:
                if line.startswith("SRC_URI[md5sum]"):
                    md5sum_line = line
                elif line.startswith("SRC_URI[sha256sum]"):
                    sha256sum_line = line

        if md5sum_line is None or sha256sum_line is None:
            E(" %s: Fetch error, not checksum related!" % self.pn)
            return self.ERR_FETCH

        I(" %s: Update recipe checksums, remove PR, etc ..." % self.pn)
        with open(self.env['FILE'] + ".tmp", "w+") as temp_recipe:
            with open(self.env['FILE']) as recipe:
                for line in recipe:
                    if line.startswith("SRC_URI[md5sum]"):
                        temp_recipe.write(md5sum_line)
                    elif line.startswith("SRC_URI[sha256sum]"):
                        temp_recipe.write(sha256sum_line)
                    elif line.startswith("PR=") or line.startswith("PR ="):
                        continue
                    else:
                        temp_recipe.write(line)

        os.rename(self.env['FILE'] + ".tmp", self.env['FILE'])

        self.checksums_changed = True

        return self.ERR_NONE

    def handle_error_do_patch(self, patch_log):
        patches_removed = False
        commit_msg = "\n\nRemoved the following backported patch(es):\n"

        for uri in self.env['SRC_URI'].split():
            if not uri.startswith("file://"):
                continue

            patch_file = uri.split("//")[1]

            # delete the file, if it's a backported patch
            recipe_dir = os.path.dirname(self.env['FILE'])
            dirs = [self.pn + "-" + self.env['PV'], self.pn, "files"]
            for dir in dirs:
                patch_file_path = os.path.join(recipe_dir, dir, patch_file)
                if not os.path.exists(patch_file_path):
                    continue

                patch_delete = False
                with open(patch_file_path) as patch:
                    for line in patch:
                        if line.find("Upstream-Status: Backport") != -1:
                            patch_delete = True
                            break

                if patch_delete:
                    os.remove(patch_file_path)
                    patches_removed = True

                    # if the patches directory is empty, remove it
                    try:
                        os.rmdir(os.path.join(recipe_dir, dir))
                    except OSError:
                        pass

                    break

            # if patch was not backported, no reason to change recipe
            if not patch_delete:
                continue

            with open(self.env['FILE'] + ".tmp", "w+") as temp_recipe:
                with open(self.env['FILE']) as recipe:
                    for line in recipe:
                        if line.find(uri) == -1:
                            temp_recipe.write(line)
                            continue

                        m1 = re.match("SRC_URI *\+*= *\" *" + uri + " *\"", line)
                        m2 = re.match("(SRC_URI *\+*= *\" *)" + uri + " *\\\\", line)
                        m3 = re.match("[\t ]*" + uri + " *\\\\", line)
                        m4 = re.match("([\t ]*)" + uri + " *\"", line)

                        # patch on a single SRC_URI line:
                        if m1 is not None:
                            continue
                        # patch is on the first SRC_URI line
                        elif m2 is not None:
                            temp_recipe.write(m2.group(1) + "\\")
                        # patch is in the middle
                        elif m3 is not None:
                            continue
                        # patch is last in list
                        elif m4 is not None:
                            temp_recipe.write(m4.group(1) + "\"")
                        # nothing matched in recipe but we deleted the patch
                        # anyway? Then we must bail out!
                        else:
                            return self.ERR_PATCH

            os.rename(self.env['FILE'] + ".tmp", self.env['FILE'])

            commit_msg += " * " + patch_file + "\n"

        # if we removed any backported patches, return 0, so we can
        # re-compile and see what happens
        if patches_removed:
            self.commit_msg += commit_msg
            return self.ERR_PATCH_BACKPORTED

        return self.ERR_PATCH

    def handle_error_do_configure(self, config_log):
        license_file = None

        with open(config_log) as log:
            for line in log:
                if not line.startswith("ERROR:"):
                    continue

                m_old = re.match("ERROR: " + self.pn + ": md5 data is not matching for file://([^;]*);md5=(.*)$", line)
                m_old_lines = re.match("ERROR: " + self.pn + ": md5 data is not matching for file://([^;]*);beginline=[0-9]*;endline=[0-9]*;md5=(.*)$", line)
                m_new = re.match("ERROR: " + self.pn + ": The new md5 checksum is (.*)", line)
                if m_old is not None:
                    license_file = m_old.group(1)
                    old_md5 = m_old.group(2)
                elif m_old_lines is not None:
                    license_file = m_old_lines.group(1)
                    old_md5 = m_old_lines.group(2)
                elif m_new is not None:
                    new_md5 = m_new.group(1)

        if license_file is not None:
            self.create_diff_file(license_file, old_md5, new_md5)
            E(" %s: license checksum failed for file %s. New checksum is: %s!" %
                (self.pn, license_file, new_md5))
            return self.ERR_LICENSE

        return self.ERR_CONFIGURE

    def handle_error_do_compile(self, compile_log):
        return self.ERR_COMPILATION

    def handle_error(self, err, stdout, stderr, expect_error=False):
        if expect_error and err == 0:
            return self.ERR_OTHER
        elif err == 0:
            return self.ERR_NONE

        failed_task_name = None
        failed_task_log = None
        machine = None

        for line in stdout.split("\n"):
            D(" %s: %s" % (self.pn, line))

            machine_match = re.match("MACHINE[\t ]+= *\"(.*)\"$", line)
            task_log_match = re.match("ERROR: Logfile of failure stored in: (.*log\.(.*)\.[0-9]*)", line)
            incomp_host = re.match("ERROR: " + self.pn + " was skipped: incompatible with host (.*) \(.*$", line)

            if task_log_match is not None:
                failed_task_log = task_log_match.group(1)
                failed_task_name = task_log_match.group(2)
            elif machine_match is not None:
                machine = machine_match.group(1)
            elif incomp_host is not None:
                W(" %s: compilation failed: incompatible host %s" % (self.pn, incomp_host.group(1)))
                return self.ERR_NONE

        if failed_task_name is None:
            E(" %s: unable to extract failed task name from stdout!" % self.pn)
            return self.ERR_OTHER

        ret = self.error_handler[failed_task_name](failed_task_log)

        if ret != self.ERR_NONE:
            W(" %s: task %s failed, copying log to %s" % (self.pn, failed_task_name, self.workdir))
            os.symlink(failed_task_log, os.path.join(self.workdir, machine + "_log." + failed_task_name))
            with open(os.path.join(self.workdir, "bitbake.log"), "w+") as log:
                log.write(stdout)

        return ret

    def create_workdir(self):
        self.workdir = self.apu_dir + "/" + self.pn

        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        else:
            for f in os.listdir(self.workdir):
                os.remove(os.path.join(self.workdir, f))

        return self.ERR_NONE

    def stash_uncommited_work(self):
        self.recipe_dir = os.path.dirname(self.env['FILE'])

        self.git = Git(self.recipe_dir)

        if self.git.stash() == (-1,):
            E(" %s: Stash failed!" % self.pn)
            return self.ERR_OTHER

        return self.ERR_NONE

    def unpack_package(self):
        return self.handle_error(*self.bb.unpack(self.pn))

    def fetch_package(self):
        if self.to_ver == self.env['PV'] and not self.checksums_changed:
            return self.handle_error(*self.bb.fetch(self.pn), expect_error=True)
        else:
            return self.handle_error(*self.bb.fetch(self.pn))

    def cleanall_package(self):
        return self.handle_error(*self.bb.cleanall(self.pn))

    def compile_package(self):
        if self.skip_compilation:
            return self.ERR_NONE

        compilation_error = False
        for machine in self.machines:
            retry = True
            while retry:
                retry = False
                I(" %s: compiling for %s ..." % (self.pn, machine))
                err = self.handle_error(*self.bb.complete(self.pn, machine))
                if err == self.ERR_NONE:
                    continue
                elif err == self.ERR_PATCH_BACKPORTED:
                    retry = True
                    continue
                elif err == self.ERR_COMPILATION:
                    compilation_error = True
                else:
                    return err

        if compilation_error:
            return self.ERR_COMPILATION

        return self.ERR_NONE

    def upgrade(self, package_name, to_ver=None, skip_compilation=False):
        self.pn = package_name
        self.checksums_changed = False
        self.maintainer = None
        self.to_ver = to_ver
        self.skip_compilation = skip_compilation

        for step, msg, err_msg in self.upgrade_steps:
            if msg is not None:
                I(" %s: %s" % (self.pn, msg))

            err = step()
            if err:
                E("%s : %s" % (self.pn, err_msg))
                E(" %s: upgrade FAILED! Logs and/or file diffs are available in %s" % (self.pn, self.workdir))
                return err

        I(" %s: upgrade SUCCESSFUL! Commit and test!" % self.pn)

        return self.ERR_NONE


class Packages(Package):
    def __init__(self):
        super(Packages, self).__init__()
        # a list of tuples (package, ver) for each upgrade status
        self.upgrade_stats = [
            ([], "Successfully upgraded: "),
            ([], "Failed at do_fetch: "),
            ([], "Failed at do_patch: "),
            ([], None),  # we'll never have entries here (backported patches)
            ([], "Failed at do_configure: "),
            ([], "Failed at do_compile: "),
            ([], "Failed license: "),
            ([], "Upgrade not needed: "),
            ([], "SRC_URI protocol not supported: "),
            ([], "Other errors: ")
        ]

    def create_stat_msg(self):
        total_pkgs = 0
        self.stat_msg = "====================================================\n"
        self.stat_msg += "Upgrade statistics:\n"
        self.stat_msg += "====================================================\n"
        for l, msg in self.upgrade_stats:
            list_len = len(l)
            if list_len > 0:
                total_pkgs += list_len
                self.stat_msg += "----------------------------------------------------\n"
                self.stat_msg += msg + " " + str(list_len) + "\n"
                self.stat_msg += "----------------------------------------------------\n"
                for pkg, to_ver, maintainer in l:
                    self.stat_msg += pkg + ", " + to_ver + ", " + maintainer + "\n"

        self.stat_msg += "++++++++++++++++++++++++++++++++++++++++++++++++++++\n"
        self.stat_msg += "TOTAL: " + str(total_pkgs) + "\n"

        return 0

    def send_status_mail(self):
        return 0

    def print_stats(self):
        print("%s" % self.stat_msg)
        return 0

    def upgrade(self, package_list):
        for pn, to_ver in package_list:
            err = super(Packages, self).upgrade(pn, to_ver)

            I(" %s: Auto commit changes ..." % self.pn)
            self.git.commit(self.commit_msg)
            I(" %s: Save patch in %s." % (self.pn, self.workdir))
            self.git.create_patch(self.workdir)

            if err != self.ERR_NONE:
                I(" %s: Since upgrade was not successful, drop changes ..." % self.pn)
                self.git.reset_hard(1)

            self.upgrade_stats[err][0].append((pn, self.to_ver, self.maintainer))

        self.create_stat_msg()

        self.print_stats()

#[lp]
#[lp]
#[lp]class Universe(List):
#[lp]    def __init__(self):
#[lp]        self.get_packages_to_upgrade()


def parse_cmdline():
    parser = argparse.ArgumentParser(description='Auto Upgrade Packages')
    parser.add_argument("package", nargs="+", help="package to be upgraded")
    parser.add_argument("-t", "--to_version",
                        help="version to upgrade the package to")
    parser.add_argument("-m", "--send_mail",
                        help="send mail when finished", action="store_true")
    parser.add_argument("-d", "--debug-level", type=int, default=4, choices=range(1, 6),
                        help="set the debug level: CRITICAL=1, ERROR=2, WARNING=3, INFO=4, DEBUG=5")
    parser.add_argument("-s", "--skip-compilation", action="store_true", default=False,
                        help="do not compile, just change the checksums, remove PR, and commit")
    return parser.parse_args()

if __name__ == "__main__":
    debug_levels = [log.CRITICAL, log.ERROR, log.WARNING, log.INFO, log.DEBUG]
    args = parse_cmdline()
    log.basicConfig(format='%(levelname)s:%(message)s',
                    level=debug_levels[args.debug_level - 1])

    if not os.getenv('BUILDDIR', False):
        E(" You must source oe-init-build-env before running this script!\n")
        exit(1)

    if len(args.package) > 1:
        pkg_list = []
        for pkg in args.package:
            pkg_list.append((pkg, None))
        pkgs = Packages()
        pkgs.upgrade(pkg_list)
    else:
        if args.package[0] == "all":
            pass
        else:
            pkg = Package()
            pkg.upgrade(args.package[0], args.to_version, args.skip_compilation)
