#!/usr/bin/env python
# vim: set ts=4 sw=4 et:
#
# Copyright (c) 2013 - 2014 Intel Corporation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
#
# DESCRIPTION
#  This is a package upgrade helper script for the Yocto Project.
#  Use 'upgrade-helper.py -h' for more help.
#
# AUTHORS
# Laurentiu Palcu <laurentiu.palcu@intel.com>
# Marius Avram <marius.avram@intel.com>
#

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
import ConfigParser as cp
from smtplib import SMTP
import mimetypes
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.generator import Generator
from datetime import datetime
from datetime import date
import shutil
from cStringIO import StringIO

for path in os.environ["PATH"].split(':'):
    if os.path.exists(path) and "bitbake" in os.listdir(path):
        sys.path.insert(0, os.path.join(path, "../lib"))
        import bb

if not os.getenv('BUILDDIR', False):
    E(" You must source oe-init-build-env before running this script!\n")
    exit(1)


help_text = \
    "Usage examples:\n\n" \
    "* To upgrade xmodmap package to the latest available version, interactively:\n" \
    "    $ upgrade-helper.py xmodmap\n\n" \
    "* To upgrade xmodmap package to a user specified version, interactively:\n" \
    "    $ upgrade-helper.py xmodmap -t 1.2.3\n\n" \
    "* To upgrade a list of packages in automatic mode:\n" \
    "    $ upgrade-helper.py -a xmodmap xf86-video-intel\n\n" \
    "* To attempt to upgrade all packages and automatically send email messages\n" \
    "  to maintainers for each attempted package as well as a status mail at the\n" \
    "  end, use:\n" \
    "    $ upgrade-helper.py all\n\n" \
    "  For this to work properly, an upgrade-helper.conf file has to be prepared,\n" \
    "  in BUILDIR/conf/upgrade-helper, as below:\n\n" \
    "   [maintainer_override]\n" \
    "   # mails for package upgrades will go to john.doe instead of jane.doe, etc\n" \
    "   jane.doe@doe.com=john.doe@doe.com\n" \
    "   johhny.bravo@bravo.com=john.doe@doe.com\n\n" \
    "   [settings]\n" \
    "   # packages in blacklist will be skipped\n" \
    "   blacklist=python glibc gcc\n" \
    "   # only packages belonging to maintainers in whitelist will be attempted\n" \
    "   maintainers_whitelist=jane.doe@doe.com john.doe@doe.com johhny.bravo@bravo.com\n" \
    "   smtp=smtp.my-server.com:25\n" \
    "   # from whom should the mails arrive\n" \
    "   from=upgrade.helper@my-server.com\n" \
    "   # who should get the status mail with statistics, at the end\n" \
    "   status_recipients=john.doe@doe.com\n" \
    "   # clean sstate directory before upgrading\n" \
    "   clean_sstate=yes\n" \
    "   # clean tmp directory before upgrading\n" \
    "   clean_tmp=yes\n" \
    "   # keep previous commits or not\n" \
    "   drop_previous_commits=yes\n"


def parse_cmdline():
    parser = argparse.ArgumentParser(description='Package Upgrade Helper',
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     epilog=help_text)
    parser.add_argument("package", nargs="+", help="package to be upgraded")
    parser.add_argument("-t", "--to_version",
                        help="version to upgrade the package to")
    parser.add_argument("-a", "--auto_mode", action="store_true", default=False,
                        help="disable interactive mode")
    parser.add_argument("-d", "--debug-level", type=int, default=4, choices=range(1, 6),
                        help="set the debug level: CRITICAL=1, ERROR=2, WARNING=3, INFO=4, DEBUG=5")
    parser.add_argument("-s", "--skip-compilation", action="store_true", default=False,
                        help="do not compile, just change the checksums, remove PR, and commit")
    parser.add_argument("-c", "--config-file", default="upgrade-helper.conf",
                        help="Path to the configuration file. Default is BUILDDIR/upgrade-helper/upgrade-helper.py")
    return parser.parse_args()


def get_build_dir():
    return os.getenv('BUILDDIR')


def parse_config_file(config_file):
    settings = dict()
    maintainer_override = dict()

    if config_file is not None:
        if os.path.exists(config_file):
            cfg_file = config_file
        else:
            cfg_file = os.path.join(get_build_dir(), "upgrade-helper", config_file)
    else:
        cfg_file = os.path.join(get_build_dir(), "upgrade-helper", "upgrade-helper.conf")

    if os.path.exists(cfg_file):
        cfg = cp.ConfigParser()
        cfg.read(cfg_file)
        try:
            settings_list = cfg.items("settings")
            for s in settings_list:
                settings[s[0]] = s[1]
        except:
            pass

        try:
            maintainer_override_list = cfg.items("maintainer_override")
            for item in maintainer_override_list:
                maintainer_override[item[0]] = item[1]
        except:
            pass

    return (settings, maintainer_override)


class Error(Exception):
    def __init__(self, message=None, stdout=None, stderr=None):
        self.message = message
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self):
        return "Failed(other errors)"


class FetchError(Error):
    def __init__(self):
        super(FetchError, self).__init__("do_fetch failed")

    def __str__(self):
        return "Failed(do_fetch)"


class PatchError(Error):
    def __init__(self):
        super(PatchError, self).__init__("do_patch failed")

    def __str__(self):
        return "Failed(do_patch)"


class ConfigureError(Error):
    def __init__(self):
        super(ConfigureError, self).__init__("do_configure failed")

    def __str__(self):
        return "Failed(do_configure)"


class CompilationError(Error):
    def __init__(self):
        super(CompilationError, self).__init__("do_compile failed")

    def __str__(self):
        return "Failed(do_compile)"


class LicenseError(Error):
    def __init__(self):
        super(LicenseError, self).__init__("license checksum does not match")

    def __str__(self):
        return "Failed(license issue)"


class UnsupportedProtocolError(Error):
    def __init__(self):
        super(UnsupportedProtocolError, self).__init__("SRC_URI protocol not supported")

    def __str__(self):
        return "Failed(Unsupported protocol)"


class UpgradeNotNeededError(Error):
    def __init__(self):
        super(UpgradeNotNeededError, self).__init__("Recipe already up to date")

    def __str__(self):
        return "Failed(up to date)"


class Git(object):
    def __init__(self, dir):
        self.repo_dir = dir
        super(Git, self).__init__()

    def _cmd(self, operation):
        os.chdir(self.repo_dir)

        cmd = "git " + operation
        try:
            stdout, stderr = bb.process.run(cmd)
        except bb.process.ExecutionError as e:
            D("%s returned:\n%s" % (cmd, e.__str__()))
            raise Error("The following git command failed: " + operation,
                        e.stdout, e.stderr)

        return stdout

    def mv(self, src, dest):
        return self._cmd("mv -f " + src + " " + dest)

    def stash(self):
        return self._cmd("stash")

    def commit(self, commit_message, author=None):
        if author is None:
            return self._cmd("commit -a -s -m \"" + commit_message + "\"")
        else:
            return self._cmd("commit -a --author=\"" + author + "\" -m \"" + commit_message + "\"")

    def create_patch(self, out_dir):
        return self._cmd("format-patch -M10 -1 -o " + out_dir)

    def status(self):
        return self._cmd("status --porcelain")

    def checkout_branch(self, branch_name):
        return self._cmd("checkout " + branch_name)

    def create_branch(self, branch_name):
        return self._cmd("checkout -b " + branch_name)

    def delete_branch(self, branch_name):
        return self._cmd("branch -D " + branch_name)

    def pull(self):
        return self._cmd("pull")

    def reset_hard(self, no_of_patches=0):
        if no_of_patches == 0:
            return self._cmd("reset --hard HEAD")
        else:
            return self._cmd("reset --hard HEAD~" + str(no_of_patches))

    def reset_soft(self, no_of_patches):
        return self._cmd("reset --soft HEAD~" + str(no_of_patches))

    def clean_untracked(self):
        return self._cmd("clean -fd")

    def last_commit(self, branch_name):
        return self._cmd("log --pretty=format:\"%H\" -1" + branch_name)

    def ls_remote(self, repo_url=None, options=None, refs=None):
        cmd = "ls-remote"
        if options is not None:
            cmd += " " + options
        if repo_url is not None:
            cmd += " " + repo_url
        if refs is not None:
            cmd += " " + refs
        return self._cmd(cmd)


class Bitbake(object):
    def __init__(self, build_dir):
        self.build_dir = build_dir
        self.log_dir = None
        super(Bitbake, self).__init__()

    def _cmd(self, recipe, options=None, env_var=None):
        cmd = ""
        if env_var is not None:
            cmd += env_var + " "
        cmd += "bitbake "
        if options is not None:
            cmd += options + " "

        cmd += recipe

        os.chdir(self.build_dir)

        try:
            stdout, stderr = bb.process.run(cmd)
        except bb.process.ExecutionError as e:
            D("%s returned:\n%s" % (cmd, e.__str__()))

            if self.log_dir is not None and os.path.exists(self.log_dir):
                with open(os.path.join(self.log_dir, "bitbake_log.txt"), "w+") as log:
                    log.write(e.stdout)

            raise Error("\'" + cmd + "\' failed", e.stdout, e.stderr)

        return stdout

    def set_log_dir(self, dir):
        self.log_dir = dir

    def env(self, recipe):
        return self._cmd(recipe, "-e")

    def fetch(self, recipe):
        return self._cmd(recipe, "-c fetch")

    def unpack(self, recipe):
        return self._cmd(recipe, "-c unpack")

    def checkpkg(self, recipe):
        if recipe == "universe":
            return self._cmd(recipe, "-c checkpkg -k")
        else:
            return self._cmd(recipe, "-c checkpkg")

    def cleanall(self, recipe):
        return self._cmd(recipe, "-c cleanall")

    def cleansstate(self, recipe):
        return self._cmd(recipe, "-c cleansstate")

    def complete(self, recipe, machine):
        return self._cmd(recipe, env_var="MACHINE=" + machine)

    def dependency_graph(self, package_list):
        return self._cmd(package_list, "-g")


class Email(object):
    def __init__(self):
        self.smtp_host = None
        self.smtp_port = None
        self.from_addr = None
        if "smtp" in settings:
            smtp_entry = settings["smtp"].split(":")
            if len(smtp_entry) == 1:
                self.smtp_host = smtp_entry[0]
                self.smtp_port = 25
            elif len(smtp_entry) == 2:
                self.smtp_host = smtp_entry[0]
                self.smtp_port = smtp_entry[1]
        else:
            E(" smtp host not set! Sending emails disabled!")

        if "from" in settings:
            self.from_addr = settings["from"]
        else:
            E(" 'From' address not set! Sending emails disabled!")

        super(Email, self).__init__()

    def send_email(self, to_addr, subject, text, files=[]):
        if self.smtp_host is None or self.from_addr is None:
            return 0

        I(" Sending email to: %s" % to_addr)

        msg = MIMEMultipart()
        msg['From'] = self.from_addr
        if type(to_addr) is list:
            msg['To'] = ', '.join(to_addr)
        else:
            msg['To'] = to_addr
        msg['Subject'] = subject

        msg.attach(MIMEText(text))

        for file in files:
            ctype, encoding = mimetypes.guess_type(file)
            if ctype is None or encoding is not None:
                ctype = 'application/octet-stream'
            maintype, subtype = ctype.split('/', 1)

            if maintype == "text":
                attachment = MIMEText(open(file).read(), _subtype=subtype)
            else:
                attachment = MIMEBase(maintype, _subtype=subtype)
                attachment.set_payload(open(file, 'rb').read())

            attachment.add_header('Content-Disposition', 'attachment; filename="%s"'
                                  % os.path.basename(file))
            msg.attach(attachment)

        out = StringIO()
        Generator(out, mangle_from_=False).flatten(msg)
        msg_text = out.getvalue()

        try:
            smtp = SMTP(self.smtp_host, self.smtp_port)
            smtp.sendmail(self.from_addr, to_addr, msg_text)
            smtp.close()
        except Exception as e:
            E("Could not send email: %s" % str(e))


class Recipe(object):
    def __init__(self, env, new_ver, interactive, workdir, recipe_dir, bitbake, git):
        self.env = env
        self.new_ver = new_ver
        self.interactive = interactive
        self.workdir = workdir
        self.recipe_dir = recipe_dir
        self.bb = bitbake
        self.bb.set_log_dir(workdir)
        self.git = git

        self.retried_recipes = set()
        self.license_diff_file = None

        self.recipes_renamed = False
        self.checksums_changed = False

        self.removed_patches = False

        self.suffixes = [
            "tar.gz", "tgz", "zip", "tar.bz2", "tar.xz", "tar.lz4", "bz2",
            "lz4", "orig.tar.gz", "src.tar.gz", "src.rpm", "src.tgz",
            "svnr\d+.tar.bz2", "stable.tar.gz", "src.rpm"]
        self.suffix_index = 0
        self.old_env = None

        self.commit_msg = self.env['PN'] + ": upgrade to " + self.new_ver + "\n\n"
        self.rm_patches_msg = "\n\nRemoved the following patch(es):\n"

        super(Recipe, self).__init__()

    def update_env(self, env):
        self.env = env

    def _rename_files_dir(self, old_ver, new_ver):
        # The files directory is renamed only if the previous
        # one has the following format PackageName-PackageVersion.
        # Otherwise is kept the same way.
        src_dir = os.path.join(self.recipe_dir, self.env['PN'] + "-" + old_ver)
        dest_dir = os.path.join(self.recipe_dir, self.env['PN'] + "-" + new_ver)

        if os.path.exists(src_dir) and os.path.isdir(src_dir):
            self.git.mv(src_dir, dest_dir)

    def rename(self):
        # change PR before renaming
        for f in os.listdir(self.recipe_dir):
            full_path_f = os.path.join(self.recipe_dir, f)
            if os.path.isfile(full_path_f) and \
                    ((f.find(self.env['PN']) == 0 and f.find(self.env['PKGV']) != -1 and
                      f.find(".bb") != -1) or
                     (f.find(self.env['PN']) == 0 and f.find(".inc") != -1)):
                with open(full_path_f + ".tmp", "w+") as temp_recipe:
                    with open(full_path_f) as recipe:
                        for line in recipe:
                            if line.startswith("PR=") or line.startswith("PR ="):
                                continue
                            else:
                                temp_recipe.write(line)
                os.rename(full_path_f + ".tmp", full_path_f)

        # rename recipes (not directories)
        for path in os.listdir(self.recipe_dir):
            full_path = os.path.join(self.recipe_dir, path)
            if os.path.isfile(full_path) \
              and path.find(self.env['PN']) == 0 \
              and path.find(self.env['PKGV']) != -1:
                new_path = re.sub(re.escape(self.env['PKGV']), self.new_ver, path)
                self.git.mv(os.path.join(self.recipe_dir, path),
                            os.path.join(self.recipe_dir, new_path))

        # rename files/PN-PV directories to PN
        self._rename_files_dir(self.env['PKGV'], self.new_ver)

        self.recipes_renamed = True

        # since we did some renaming, backup the current environment
        self.old_env = self.env

        # start formatting the commit message

    def create_diff_file(self, file, old_md5, new_md5):
        old_file = os.path.join(self.old_env['S'], file)
        new_file = os.path.join(self.env['S'], file)
        cmd = "diff -Nup " + old_file + " " + new_file + " > " + \
              os.path.join(self.workdir, os.path.basename(file + ".diff"))

        try:
            stdout, stderr = bb.process.run(cmd)
        except bb.process.ExecutionError:
            pass

        with open(os.path.join(self.workdir, "license_checksums.txt"), "w+") as f:
            f.write("old checksum = %s\n" % old_md5)
            f.write("new_checksum = %s\n" % new_md5)

        for f in os.listdir(self.recipe_dir):
            full_path_f = os.path.join(self.recipe_dir, f)
            if os.path.isfile(full_path_f) and \
                    ((f.find(self.env['PN']) == 0 and
                      f.find(self.env['PKGV']) != -1 and
                      f.find(".bb") != -1) or
                     (f.find(self.env['PN']) == 0 and
                      f.find(".inc") != -1)):
                with open(full_path_f + ".tmp", "w+") as temp_recipe:
                    with open(full_path_f) as recipe:
                        for line in recipe:
                            m = re.match("(.*)" + old_md5 + "(.*)", line)
                            if m is not None:
                                temp_recipe.write(m.group(1) + new_md5 + m.group(2) + "\n")
                            else:
                                temp_recipe.write(line)

                os.rename(full_path_f + ".tmp", full_path_f)

    def _change_recipe_checksums(self, fetch_log):
        sums = {}

        with open(os.path.realpath(fetch_log)) as log:
            for line in log:
                m = None
                key = None
                m1 = re.match("^SRC_URI\[(.*)md5sum\].*", line)
                m2 = re.match("^SRC_URI\[(.*)sha256sum\].*", line)
                if m1:
                    m = m1
                    key = "md5sum"
                elif m2:
                    m = m2
                    key = "sha256sum"

                if m:
                    name = m.group(1)
                    sum_line = m.group(0) + '\n'
                    if name not in sums:
                        sums[name] = {}
                    sums[name][key] = sum_line;

        if len(sums) == 0:
            raise FetchError()

        I(" %s: Update recipe checksums ..." % self.env['PN'])
        # checksums are usually in the main recipe but they can also be in inc
        # files... Go through the recipes/inc files until we find them
        for f in os.listdir(self.recipe_dir):
            full_path_f = os.path.join(self.recipe_dir, f)
            if os.path.isfile(full_path_f) and \
                    ((f.find(self.env['PN']) == 0 and f.find(self.env['PKGV']) != -1 and
                      f.find(".bb") != -1) or
                     (f.find(self.env['PN']) == 0 and f.find(".inc") != -1)):
                with open(full_path_f + ".tmp", "w+") as temp_recipe:
                    with open(full_path_f) as recipe:
                        for line in recipe:
                            for name in sums:
                                m1 = re.match("^SRC_URI\["+ name + "md5sum\].*", line)
                                m2 = re.match("^SRC_URI\["+ name + "sha256sum\].*", line)
                                if m1:
                                    temp_recipe.write(sums[name]["md5sum"])
                                elif m2:
                                    temp_recipe.write(sums[name]["sha256sum"])
                                else:
                                    temp_recipe.write(line)

                os.rename(full_path_f + ".tmp", full_path_f)

        self.checksums_changed = True

    def _is_uri_failure(self, fetch_log):
        uri_failure = None
        checksum_failure = None
        with open(os.path.realpath(fetch_log)) as log:
            for line in log:
                if not uri_failure:
                    uri_failure = re.match(".*Fetcher failure for URL.*", line)
                if not checksum_failure:
                    checksum_failure = re.match(".*Checksum mismatch.*", line)
        if uri_failure and not checksum_failure:
            return True
        else:
            return False


    def _change_source_suffix(self, new_suffix):
        # Will change the extension of the archive from the SRC_URI
        for f in os.listdir(self.recipe_dir):
            full_path_f = os.path.join(self.recipe_dir, f)
            if os.path.isfile(full_path_f) and \
                    ((f.find(self.env['PN']) == 0 and f.find(self.env['PKGV']) != -1 and
                      f.find(".bb") != -1) or
                     (f.find(self.env['PN']) == 0 and f.find(".inc") != -1)):
                with open(full_path_f + ".tmp", "w+") as temp_recipe:
                    with open(full_path_f) as recipe:
                        for line in recipe:
                            m = re.match("^SRC_URI.*\${PV}\.(.*)[\" \\\\].*", line)
                            if m:
                                old_suffix = m.group(1)
                                line = line.replace(old_suffix, new_suffix+" ")
                            temp_recipe.write(line)
                os.rename(full_path_f + ".tmp", full_path_f)

    def _remove_patch_uri(self, uri):
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
                        temp_recipe.write(m2.group(1) + "\\\n")
                    # patch is in the middle
                    elif m3 is not None:
                        continue
                    # patch is last in list
                    elif m4 is not None:
                        temp_recipe.write(m4.group(1) + "\"\n")
                    # nothing matched in recipe but we deleted the patch
                    # anyway? Then we must bail out!
                    else:
                        return False

        os.rename(self.env['FILE'] + ".tmp", self.env['FILE'])

    def _remove_backported_patches(self, patch_log):
        patches_removed = False
        commit_msg = "\n\nRemoved the following patch(es):\n"

        reverse_applied = []

        with open(patch_log) as log:
            for line in log:
                m = re.match("Patch ([^ ]*) can be reverse-applied", line)
                if m is not None:
                    reverse_applied.append(m.group(1))

        for uri in self.env['SRC_URI'].split():
            if not uri.startswith("file://"):
                continue

            patch_file = uri.split("//")[1]
            remove_reason = "backport"
            patch_delete = False

            # delete the file, if it's a backported patch
            dirs = [self.env['PN'] + "-" + self.env['PKGV'], self.env['PN'], "files"]
            for dir in dirs:
                patch_file_path = os.path.join(self.recipe_dir, dir, patch_file)
                if not os.path.exists(patch_file_path):
                    continue

                with open(patch_file_path) as patch:
                    for line in patch:
                        if line.find("Upstream-Status: Backport") != -1:
                            patch_delete = True
                            break

                if not patch_delete and patch_file in reverse_applied:
                    patch_delete = True
                    remove_reason = "changes included in release"

                if patch_delete:
                    os.remove(patch_file_path)
                    patches_removed = True

                    # if the patches directory is empty, remove it
                    try:
                        os.rmdir(os.path.join(self.recipe_dir, dir))
                    except OSError:
                        pass

                    break

            if not patch_delete:
                continue

            self._remove_patch_uri(uri)

            commit_msg += " * " + patch_file + " (" + remove_reason + ")\n"

        commit_msg += "\n"

        # if we removed any backported patches, return 0, so we can
        # re-compile and see what happens
        if patches_removed:
            I(" %s: removed some backported patches, retrying ...", self.env['PN'])
            self.commit_msg += commit_msg
            return True

        return False

    def _remove_faulty_patch(self, patch_log):
        patch_file = None
        with open(patch_log) as log:
            for line in log:
                m = re.match("^Patch (.*) does not apply.*", line)
                if m:
                    patch_file = m.group(1)
                    break

        if not patch_file:
            return False

        I(" %s: Removing patch %s ..." % (self.env['PN'], patch_file))
        dirs = [self.env['PN'] + "-" + self.env['PKGV'], self.env['PN'], "files"]
        for dir in dirs:
            patch_file_path = os.path.join(self.recipe_dir, dir, patch_file)
            if not os.path.exists(patch_file_path):
                continue
            else:
                # Find out upstream status of the patch
                with open(patch_file_path) as patch:
                    for line in patch:
                        m = re.match(".*Upstream-Status:(.*)\n", line)
                        if m:
                            reason = m.group(1).strip().split()[0].lower()
                os.remove(patch_file_path)
                self._remove_patch_uri("file://" + patch_file)

        self.rm_patches_msg += " * " + patch_file + " (" + reason + ") \n"

    def _is_license_issue(self, config_log):
        with open(config_log) as log:
            for line in log:
                m = re.match("ERROR: " + self.env['PN'] +
                             "[^:]*: md5 data is not matching for file", line)
                if m is not None:
                    return True

        return False

    def _license_issue_handled(self, config_log):
        license_file = None

        with open(config_log) as log:
            for line in log:
                if not line.startswith("ERROR:"):
                    continue

                m_old = re.match("ERROR: " + self.env['PN'] +
                        "[^:]*: md5 data is not matching for file://([^;]*);md5=(.*)$", line)
                if not m_old:
                    m_old = re.match("ERROR: " + self.env['PN'] +
                            "[^:]*: md5 data is not matching for file://([^;]*);beginline=[0-9]*;endline=[0-9]*;md5=(.*)$", line)
                if not m_old:
                    m_old = re.match("ERROR: " + self.env['PN'] +
                            "[^:]*: md5 data is not matching for file://([^;]*);endline=[0-9]*;md5=(.*)$", line)
                m_new = re.match("ERROR: " + self.env['PN'] +
                        "[^:]*: The new md5 checksum is (.*)", line)
                if m_old:
                    license_file = m_old.group(1)
                    old_md5 = m_old.group(2)
                elif m_new:
                    new_md5 = m_new.group(1)

        if license_file is not None:
            self.create_diff_file(license_file, old_md5, new_md5)
            self.license_diff_file = os.path.join(self.workdir, os.path.basename(license_file + ".diff"))
            if self.interactive:
                W("  %s: license checksum failed for file %s. The recipe has"
                  "been updated! View diff? (Y/n)" % (self.env['PN'], license_file))
                answer = sys.stdin.readline().strip().upper()
                if answer == '' or answer == 'Y':
                    print(" ################ Licence file diff #################")
                    with open(self.license_diff_file) as diff:
                        print("%s" % diff.read())
                    print(" ####################################################")
                print("Retry compilation? (Y/n)")
                answer = sys.stdin.readline().strip().upper()
                if answer == '' or answer == 'Y':
                    return True
            else:
                W(" %s: license checksum failed for file %s."
                  " The recipe has been updated! Diff file located at %s" %
                  (self.env['PN'], license_file, self.license_diff_file))
                I(" recompiling ...")
                self.commit_msg += "License checksum changed for file " + license_file
                return True

        return False

    def _get_failed_recipes(self, output):
        failed_tasks = dict()
        machine = None

        for line in output.split("\n"):
            machine_match = re.match("MACHINE[\t ]+= *\"(.*)\"$", line)
            task_log_match = re.match("ERROR: Logfile of failure stored in: (.*/([^/]*)/[^/]*/temp/log\.(.*)\.[0-9]*)", line)

            if task_log_match is not None:
                failed_tasks[task_log_match.group(2)] = (task_log_match.group(3), task_log_match.group(1))
            elif machine_match is not None:
                machine = machine_match.group(1)

        # we didn't detect any failed tasks? then something else is wrong
        if len(failed_tasks) == 0:
            raise Error("could not detect failed task")

        return (machine, failed_tasks)

    def _is_incompatible_host(self, output):
        for line in output.split("\n"):
            incomp_host = re.match("ERROR: " + self.env['PN'] + " was skipped: incompatible with host (.*) \(.*$", line)

            if incomp_host is not None:
                return True

        return False

    def _add_not_shipped(self, package_log):
        files_not_shipped = False
        files = []
        occurences = []
        prefixes = {
          "/usr"            : "prefix",
          "/bin"            : "base_bindir",
          "/sbin"           : "base_sbindir",
          "/lib"            : "base_libdir",
          "/usr/share"      : "datadir",
          "/etc"            : "sysconfdir",
          "/var"            : "localstatedir",
          "/usr/share/info" : "infodir",
          "/usr/share/man"  : "mandir",
          "/usr/share/doc"  : "docdir",
          "/srv"            : "servicedir",
          "/usr/bin"        : "bindir",
          "/usr/sbin"       : "sbindir",
          "/usr/libexec"    : "libexecdir",
          "/usr/lib"        : "libdir",
          "/usr/include"    : "includedir",
          "/usr/lib/opie"   : "palmtopdir",
          "/usr/lib/opie"   : "palmqtdir",
        }

        with open(package_log) as log:
            for line in log:
                if re.match(".*Files/directories were installed but not shipped.*", line):
                    files_not_shipped = True
                line = line.strip()
                if files_not_shipped and os.path.isabs(line):
                    # Count occurences for globbing
                    path_exists = False
                    for i in range(0, len(files)):
                        if line.find(files[i]) == 0:
                            path_exists = True
                            occurences[i] += 1
                            break
                    if not path_exists:
                        files.append(line)
                        occurences.append(1)

        for i in range(0, len(files)):
            # Change paths to globbing expressions where is the case
            if occurences[i] > 1:
                files[i] += "/*"
            largest_prefix = ""
            # Substitute prefix
            for prefix in prefixes:
                if files[i].find(prefix) == 0 and len(prefix) > len(largest_prefix):
                    largest_prefix = prefix
            if largest_prefix:
                replacement = "${" + prefixes[largest_prefix] + "}"
                files[i] = files[i].replace(largest_prefix, replacement)

        recipe_files = [
            os.path.join(self.recipe_dir, self.env['PN'] + ".inc"),
            self.env['FILE']]

        # Append the new files
        for recipe_filename in recipe_files:
            with open(recipe_filename + ".tmp", "w+") as temp_recipe:
                with open(recipe_filename) as recipe:
                    files_clause = False
                    for line in recipe:
                        if re.match("^FILES_\${PN}[ +=].*", line):
                            files_clause = True
                            temp_recipe.write(line)
                            continue
                        # Get front spacing
                        if files_clause:
                            front_spacing = re.sub("[^ \t]", "", line)
                        # Append once the last line has of FILES has been reached
                        if re.match(".*\".*", line) and files_clause:
                            files_clause = False
                            line = line.replace("\"", "")
                            line = line.rstrip()
                            front_spacing = re.sub("[^ \t]", "", line)
                            # Do not write an empty line
                            if line.strip():
                                temp_recipe.write(line + " \\\n")
                            # Add spacing in case there was none
                            if len(front_spacing) == 0:
                                front_spacing = " " * 8
                            # Write to file
                            for i in range(len(files)-1):
                                line = front_spacing + files[i] + " \\\n"
                                temp_recipe.write(line)

                            line = front_spacing + files[len(files) - 1] + "\"\n"
                            temp_recipe.write(line)
                            continue

                        temp_recipe.write(line)

            os.rename(recipe_filename + ".tmp", recipe_filename)

    def unpack(self):
        self.bb.unpack(self.env['PN'])

    def fetch(self):
        try:
            self.bb.fetch(self.env['PN'])
        except Error as e:
            machine, failed_recipes = self._get_failed_recipes(e.stdout)
            if not self.env['PN'] in failed_recipes:
                raise Error("unknown error occured during fetch")

            fetch_log = failed_recipes[self.env['PN']][1]
            if self.suffix_index < len(self.suffixes) and self._is_uri_failure(fetch_log):
                I(" Trying new SRC_URI suffix: %s ..." % self.suffixes[self.suffix_index])
                self._change_source_suffix(self.suffixes[self.suffix_index])
                self.suffix_index += 1
                self.fetch()

            if not self.checksums_changed:
                self._change_recipe_checksums(fetch_log)
                return
            else:
                raise FetchError()

        if self.recipes_renamed and not self.checksums_changed:
            raise Error("fetch succeeded without changing checksums")

    def cleanall(self):
        self.bb.cleanall(self.env['PN'])

    def _clean_failed_recipes(self, failed_recipes):
        already_retried = False
        for recipe in failed_recipes:
            if recipe in self.retried_recipes:
                # we already retried, we'd best leave it to a human to handle
                # it :)
                already_retried = True
            # put the recipe in the retried list
            self.retried_recipes.add(recipe)

        if already_retried:
            return False
        else:
            I(" %s: The following recipe(s): %s, failed.  "
              "Doing a 'cleansstate' and then retry ..." %
              (self.env['PN'], ' '.join(failed_recipes.keys())))

            self.bb.cleansstate(' '.join(failed_recipes.keys()))
            return True

    def _undo_temporary(self):
        # Undo removed patches
        if self.removed_patches:
            self.git.checkout_branch("master")
            self.git.delete_branch("remove_patches")
            self.git.reset_hard()
            self.git.reset_soft(1)


    def compile(self, machine):
        try:
            self.bb.complete(self.env['PN'], machine)
            if self.removed_patches:
                # move temporary changes into master
                self.git.checkout_branch("master")
                self.git.delete_branch("remove_patches")
                self.git.reset_soft(1)
                self.commit_msg += self.rm_patches_msg + "\n"
        except Error as e:
            if self._is_incompatible_host(e.stdout):
                W(" %s: compilation failed: incompatible host" % self.env['PN'])
                return

            machine, failed_recipes = self._get_failed_recipes(e.stdout)
            if not self.env['PN'] in failed_recipes:
                if not self._clean_failed_recipes(failed_recipes):
                    raise CompilationError()

                # retry
                self.compile(machine)
            else:
                failed_task = failed_recipes[self.env['PN']][0]
                log_file = failed_recipes[self.env['PN']][1]
                if failed_task == "do_patch":
                    # Remove one patch after the other until
                    # compilation works.
                    if not self.removed_patches:
                        self.git.commit("temporary")
                        self.git.create_branch("remove_patches")
                        self.git.checkout_branch("remove_patches")
                        self.removed_patches = True
                    self._remove_faulty_patch(log_file)

                    # retry
                    I(" %s: Recompiling for ..." % (self.env['PN'], machine))
                    self.compile(machine)
                elif failed_task == "do_configure":
                    self._undo_temporary()
                    if not self._is_license_issue(log_file):
                        raise ConfigureError()

                    if not self._license_issue_handled(log_file):
                        raise LicenseError()

                    #retry
                    self.compile(machine)
                elif failed_task == "do_fetch":
                    raise FetchError()
                elif failed_task == "do_package":
                    self._add_not_shipped(log_file)
                    self.compile(machine)
                else:
                    self._undo_temporary()
                    # throw a compilation exception for everything else. It
                    # doesn't really matter
                    raise CompilationError()


class GitRecipe(Recipe):
    def _extract_tag_from_ver(self, ver):
        m = re.match("(.*)\+.*\+.*", ver)
        if m is not None:
            return m.group(1)

        # allow errors in the reporting system
        return ver

    def _get_tag_sha1(self, new_tag):
        print("new_git_tag %s" % new_tag)
        m = re.match(".*(git://[^ ;]*).*", self.env['SRC_URI'])
        if m is None:
            raise Error("could not extract repo url from SRC_URI")

        repo_url = m.group(1)
        print("repo_url %s" % repo_url)
        tags = self.git.ls_remote(repo_url, "--tags")

        # Try to find tag ending with ^{}
        for tag in tags.split('\n'):
            if tag.endswith(new_tag + "^{}"):
                return tag.split()[0]

        # If not found, try to find simple tag
        for tag in tags.split('\n'):
            if tag.endswith(new_tag):
                return tag.split()[0]

        return None

    def rename(self):
        old_git_tag = self._extract_tag_from_ver(self.env['PKGV'])
        new_git_tag = self._extract_tag_from_ver(self.new_ver)

        if new_git_tag == old_git_tag:
            raise UpgradeNotNeededError()

        tag_sha1 = self._get_tag_sha1(new_git_tag)
        if tag_sha1 is None:
            raise Error("could not extract tag sha1")

        for f in os.listdir(self.recipe_dir):
            full_path_f = os.path.join(self.recipe_dir, f)
            if os.path.isfile(full_path_f) and \
                    ((f.find(self.env['PN']) == 0 and (f.find(old_git_tag) != -1 or
                      f.find("git") != -1) and f.find(".bb") != -1) or
                     (f.find(self.env['PN']) == 0 and f.find(".inc") != -1)):
                with open(full_path_f + ".tmp", "w+") as temp_recipe:
                    with open(full_path_f) as recipe:
                        for line in recipe:
                            m1 = re.match("^SRCREV *= *\".*\"", line)
                            m2 = re.match("PV *= *\"[^\+]*(.*)\"", line)
                            if m1 is not None:
                                temp_recipe.write("SRCREV = \"" + tag_sha1 + "\"\n")
                            elif m2 is not None:
                                temp_recipe.write("PV = \"" + new_git_tag + m2.group(1) + "\"\n")
                            else:
                                temp_recipe.write(line)

                os.rename(full_path_f + ".tmp", full_path_f)

        self.env['PKGV'] = old_git_tag
        self.new_ver = new_git_tag

        super(GitRecipe, self).rename()

    def fetch(self):
        pass


class SvnRecipe(Recipe):
    pass


class Statistics(object):
    def __init__(self):
        self.succeeded = dict()
        self.failed = dict()
        self.succeeded["total"] = 0
        self.failed["total"] = 0
        self.upgrade_stats = dict()
        self.maintainers = set()
        self.total_attempted = 0

    def update(self, pn, new_ver, maintainer, error):
        if type(error).__name__ == "UpgradeNotNeededError":
            return
        elif error is None:
            status = "Succeeded"
        else:
            status = str(error)

        if not status in self.upgrade_stats:
            self.upgrade_stats[status] = []

        self.upgrade_stats[status].append((pn, new_ver, maintainer))

        # add maintainer to the set of unique maintainers
        self.maintainers.add(maintainer)

        if not maintainer in self.succeeded:
            self.succeeded[maintainer] = 0
        if not maintainer in self.failed:
            self.failed[maintainer] = 0

        if status == "Succeeded":
            self.succeeded["total"] += 1
            self.succeeded[maintainer] += 1
        else:
            self.failed["total"] += 1
            self.failed[maintainer] += 1

        self.total_attempted += 1

    def pkg_stats(self):
        stat_msg = "\nUpgrade statistics:\n"
        stat_msg += "====================================================\n"
        for status in self.upgrade_stats:
            list_len = len(self.upgrade_stats[status])
            if list_len > 0:
                stat_msg += "* " + status + ": " + str(list_len) + "\n"

                for pkg, new_ver, maintainer in self.upgrade_stats[status]:
                    stat_msg += "    " + pkg + ", " + new_ver + ", " + \
                                maintainer + "\n"

        stat_msg += "++++++++++++++++++++++++++++++++++++++++++++++++++++\n"
        stat_msg += "TOTAL: attempted=%d succeeded=%d(%.2f%%) failed=%d(%.2f%%)\n\n" % \
                    (self.total_attempted, self.succeeded["total"],
                    self.succeeded["total"] * 100.0 / self.total_attempted,
                    self.failed["total"],
                    self.failed["total"] * 100.0 / self.total_attempted)

        return stat_msg

    def maintainer_stats(self):
        stat_msg = "* Statistics per maintainer:\n"
        for m in self.maintainers:
            attempted = self.succeeded[m] + self.failed[m]
            stat_msg += "    %s: attempted=%d succeeded=%d(%.2f%%) failed=%d(%.2f%%)\n\n" % \
                        (m.split("@")[0], attempted, self.succeeded[m],
                        self.succeeded[m] * 100.0 / attempted,
                        self.failed[m],
                        self.failed[m] * 100.0 / attempted)

        return stat_msg


class Updater(object):
    def __init__(self, auto_mode=False, skip_compilation=False):
        self.uh_dir = get_build_dir() + "/upgrade-helper"
        if not os.path.exists(self.uh_dir):
            os.mkdir(self.uh_dir)

        self.bb = Bitbake(get_build_dir())
        self.git = None

        self.author = None
        self.skip_compilation = skip_compilation
        self.interactive = not auto_mode

        #self.machines = ["qemux86", "qemux86-64", "qemuarm", "qemumips", "qemuppc"]
        self.machines = ["qemux86"]

        self.upgrade_steps = [
            (self._create_workdir, None),
            (self._detect_repo, "Detecting git repository location ..."),
            (self._detect_recipe_type, None),
            (self._unpack_original, "Fetch & unpack original package ..."),
            (self._rename, "Renaming recipes, reset PR (if exists) ..."),
            (self._cleanall, "Clean all ..."),
            (self._fetch, "Fetch new package (old checksums) ..."),
            (self._compile, None)
        ]

        self.statistics = Statistics()

        super(Updater, self).__init__()

    def _get_env(self):
        stdout = self.bb.env(self.pn)

        assignment = re.compile("^([^ \t=]*)=(.*)")
        bb_env = dict()
        for line in stdout.split('\n'):
            m = assignment.match(line)
            if m:
                if m.group(1) in bb_env:
                    continue

                bb_env[m.group(1)] = m.group(2).strip("\"")

        self.env = bb_env
        self.recipe_dir = os.path.dirname(self.env['FILE'])

    def _parse_checkpkg_line(self, line):
        m = re.match("^([^ \t]*)[ \t]+([^ \t]*)[ \t]+([^ \t]*).*<(.*)@(.*)>[ \t]+.*", line)
        if m is not None:
            return (m.group(1), m.group(2), m.group(3), m.group(4) + "@" + m.group(5))

        return (None, None, None, None)

    def _detect_recipe_type(self):
        if self.env['SRC_URI'].find("ftp://") != -1 or  \
                self.env['SRC_URI'].find("http://") != -1 or \
                self.env['SRC_URI'].find("https://") != -1:
            recipe = Recipe
        elif self.env['SRC_URI'].find("git://") != -1:
            recipe = GitRecipe
        else:
            raise UnsupportedProtocolError

        self.recipe = recipe(self.env, self.new_ver, self.interactive, self.workdir,
                             self.recipe_dir, self.bb, self.git)

    def _create_workdir(self):
        self.workdir = self.uh_dir + "/" + self.pn

        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        else:
            for f in os.listdir(self.workdir):
                os.remove(os.path.join(self.workdir, f))

    def _detect_repo(self):
        self._get_env()

        if self.git is not None:
            return

        self.git = Git(self.recipe_dir)

        stdout = self.git.status()

        if stdout != "":
            if self.interactive:
                W(" %s: git repository has uncommited work which will be dropped! Proceed? (y/N)" % self.pn)
                answer = sys.stdin.readline().strip().upper()
                if answer == '' or answer != 'Y':
                    I(" %s: User abort!" % self.pn)
                    exit(0)

            W(" %s: Dropping uncommited work!" % self.pn)
            self.git.reset_hard()
            self.git.clean_untracked()

            self._get_env()

    def _unpack_original(self):
        self.recipe.unpack()

    def _rename(self):
        self.recipe.rename()

        # fetch new environment
        self._get_env()

        self.recipe.update_env(self.env)

    def _cleanall(self):
        self.recipe.cleanall()

    def _fetch(self):
        self.recipe.fetch()

    def _compile(self):
        if self.skip_compilation:
            W(" %s: Compilation was skipped by user choice!")
            return

        for machine in self.machines:
            I(" %s: compiling for %s ..." % (self.pn, machine))
            self.recipe.compile(machine)

    def _check_upstream_versions(self, packages=[("universe", None, None)]):
        I(" Fetching upstream version(s) ...")

        try:
            self.bb.checkpkg(" ".join([p[0] for p in packages]))
        except Error as e:
            for line in e.stdout.split('\n'):
                if line.find("ERROR: Task do_checkpkg does not exist") == 0:
                    C(" \"distrodata.bbclass\" not inherited. Consider adding "
                      "the following to your local.conf:\n\n"
                      "INHERIT =+ \"distrodata\"\n"
                      "require conf/distro/include/recipe_color.inc\n"
                      "require conf/distro/include/distro_alias.inc\n"
                      "require conf/distro/include/maintainers.inc\n"
                      "require conf/distro/include/upstream_tracking.inc\n")
                    exit(1)

    def _get_packages_to_upgrade(self, packages=None):
        if packages is None:
            return []

        if len(packages) == 1:
            # if user specified the version to upgrade to, just return the
            # tuple intact
            if packages[0][1] is not None:
                return packages

        self._check_upstream_versions(packages)

        pkgs_list = []

        with open(get_build_dir() + "/tmp/log/checkpkg.csv") as csv:
            for line in csv:
                (pn, cur_ver, next_ver, maintainer) = self._parse_checkpkg_line(line)

                if (pn, cur_ver, next_ver, maintainer) == (None, None, None, None):
                    continue

                if cur_ver != next_ver and next_ver != "N/A" and \
                        next_ver != "INVALID":
                    pkgs_list.append((pn, next_ver, maintainer))
                else:
                    W(" Skip package %s (current version = %s, next version = %s)" %
                        (pn, cur_ver, next_ver))

        return pkgs_list

    # this function will be called at the end of each package upgrade
    def pkg_upgrade_handler(self, err):
        if err is not None and self.patch_file is not None:
            answer = "N"
            if self.interactive:
                I(" %s: Do you want to keep the changes? (y/N)" % self.pn)
                answer = sys.stdin.readline().strip().upper()

            if answer == '' or answer == 'N':
                I(" %s: Dropping changes from git ..." % self.pn)
                self.git.reset_hard(1)
                self.git.clean_untracked()

    def _commit_changes(self):
        try:
            self.patch_file = None
            if self.recipe is not None:
                I(" %s: Auto commit changes ..." % self.pn)
                self.git.commit(self.recipe.commit_msg, self.author)
                I(" %s: Save patch in %s." % (self.pn, self.workdir))
                stdout = self.git.create_patch(self.workdir)
                self.patch_file = stdout.strip()
        except Error as e:
            for line in e.stdout.split("\n"):
                if line.find("nothing to commit") == 0:
                    I(" %s: Nothing to commit!" % self.pn)
                    return

            raise e

    def _order_list(self, package_list):
        try:
            self.bb.dependency_graph(' '.join(p[0] for p in package_list))
        except Error as e:
            multiple_providers = False
            for l in e.stdout.split('\n'):
                if l.find("ERROR: Multiple .bb files are due to be built which each provide") == 0:
                    multiple_providers = True

            if not multiple_providers:
                raise e

        dep_file = os.path.join(get_build_dir(), "pn-buildlist")
        ordered_list = []
        with open(dep_file) as deps:
            for d in deps:
                ordered_list.extend(p for p in package_list if p[0] == d.strip())

        return ordered_list

    def run(self, package_list=None):
#[lp]        pkgs_to_upgrade = self._order_list(self._get_packages_to_upgrade(package_list))
        pkgs_to_upgrade = self._get_packages_to_upgrade(package_list)

        total_pkgs = len(pkgs_to_upgrade)

        attempted_pkgs = 0
        for self.pn, self.new_ver, self.maintainer in pkgs_to_upgrade:
            self.recipe = None
            attempted_pkgs += 1
            I(" ATTEMPT PACKAGE %d/%d" % (attempted_pkgs, total_pkgs))
            try:
                I(" %s: upgrading to %s" % (self.pn, self.new_ver))
                for step, msg in self.upgrade_steps:
                    if msg is not None:
                        I(" %s: %s" % (self.pn, msg))
                    step()

                I(" %s: upgrade SUCCESSFUL! Please test!" % self.pn)
                error = None
            except UpgradeNotNeededError as e:
                I(" %s: %s" % (self.pn, e.message))
                error = e
            except Error as e:
                E(" %s: %s" % (self.pn, e.message))
                E(" %s: upgrade FAILED! Logs and/or file diffs are available in %s" % (self.pn, self.workdir))
                error = e

            self._commit_changes()

            self.pkg_upgrade_handler(error)

            self.statistics.update(self.pn, self.new_ver, self.maintainer, error)

            if self.interactive and attempted_pkgs < total_pkgs:
                I(" %s: Proceed to next package? (Y/n)" % self.pn)
                answer = sys.stdin.readline().strip().upper()

                if answer != 'Y' and answer != '':
                    I("Aborted by user!")
                    exit(0)

        if (attempted_pkgs > 1):
            print("%s" % self.statistics.pkg_stats())


class UniverseUpdater(Updater, Email):
    mail_header = \
        "Hello,\n\nYou are receiving this email because you are the maintainer\n" \
        "of *%s* package and this is to let you know that the automatic attempt\n" \
        "to upgrade the package to *%s* has %s.\n\n"

    next_steps_info = \
        "The package has been successfully compiled for all major architectures.\n\n" \
        "Next steps:\n" \
        "    - apply the patch: git am %s\n" \
        "    - compile an image that contains the package\n" \
        "    - perform some basic sanity tests\n" \
        "    - amend the patch and sign it off: git commit -s --reset-author --amend\n" \
        "    - send it to the list\n\n" \

    mail_footer = \
        "Attached are the patch and the logs (+ license file diff) in case of failure.\n\n" \
        "Regards,\nThe Upgrade Helper"

    def __init__(self):
        super(UniverseUpdater, self).__init__(True)
        self.author = "Upgrade Helper <uh@not.set>"
        self.git = Git(os.path.dirname(os.getenv('PATH', False).split(':')[0]))

        # read history file
        self.history_file = os.path.join(get_build_dir(), "upgrade-helper", "history.uh")
        self.history = dict()
        if os.path.exists(self.history_file):
            with open(self.history_file) as history_file:
                for line in history_file:
                    self.history[line.split(',')[0]] = [line.split(',')[1],
                                                        line.split(',')[2],
                                                        line.split(',')[3],
                                                        line.split(',')[4]]

    # checks if maintainer is in whitelist and that the package itself is not
    # blacklisted: python, gcc, etc. Also, check the history if the package
    # hasn't already been tried
    def pkg_upgradable(self, pn, next_ver, maintainer):
        if "blacklist" in settings:
            for p in settings["blacklist"].split():
                if p == pn:
                    return False

        if "maintainers_whitelist" in settings:
            found = False
            for m in settings["maintainers_whitelist"].split():
                if m == maintainer:
                    found = True
                    break

            if not found:
                return False

        if pn in self.history:
            # did we already try this version?
            if next_ver == self.history[pn][0]:
                retry_delta = \
                    date.toordinal(date.today()) - \
                    date.toordinal(datetime.strptime(self.history[pn][2], '%Y-%m-%d'))
                # retry packages that had fetch errors or other errors after
                # more than 7 days
                if (self.history[pn][3] == str(FetchError) or
                        self.history[pn][3] == str(Error)) and retry_delta > 7:
                    return True

                return False

        # drop native/cross/cross-canadian packages. We deal with native
        # when upgrading the main package but we keep away of cross* pkgs...
        # for now
        if pn.find("cross") != -1 or pn.find("native") != -1:
            return False

        return True

    def update_master(self):
        I(" Drop all uncommited changes (including untracked) ...")
        self.git.reset_hard()
        self.git.clean_untracked()

        self.git.checkout_branch("master")
        try:
            self.git.delete_branch("upgrades")
        except Error:
            pass
        I(" Sync master ...")
        self.git.pull()
        self.git.create_branch("upgrades")

    def prepare(self):
        if "clean_sstate" in settings and settings["clean_sstate"] == "yes" and \
                os.path.exists(os.path.join(get_build_dir(), "sstate-cache")):
            I(" Removing sstate directory ...")
            shutil.rmtree(os.path.join(get_build_dir(), "sstate-cache"))
        if "clean_tmp" in settings and settings["clean_tmp"] == "yes" and \
                os.path.exists(os.path.join(get_build_dir(), "tmp")):
            I(" Removing tmp directory ...")
            shutil.rmtree(os.path.join(get_build_dir(), "tmp"))

    def _get_packages_to_upgrade(self, packages=None):
        last_date_checked = None
        last_master_commit = None
        last_checkpkg_file = None
        current_date = date.isoformat(date.today())
        try:
            stdout = self.git.last_commit("master")
            cur_master_commit = stdout
        except Error:
            cur_master_commit = "unknown"

        if os.path.exists(get_build_dir() + "/upgrade-helper/last_checkpkg_run"):
            with open(get_build_dir() + "/upgrade-helper/last_checkpkg_run") as last_check:
                line = last_check.read()
                last_date_checked = line.split(',')[0]
                last_master_commit = line.split(',')[1]
                last_checkpkg_file = line.split(',')[2]
                if not os.path.exists(last_checkpkg_file):
                    last_checkpkg_file = None

        if last_master_commit != cur_master_commit or last_date_checked != current_date or \
                last_checkpkg_file is None:
            self._check_upstream_versions()
            last_checkpkg_file = os.path.realpath(get_build_dir() + "/tmp/log/checkpkg.csv")
        else:
            I(" Using last checkpkg.csv file since last master commit and last"
              " check date are the same ...")

        with open(get_build_dir() + "/upgrade-helper/last_checkpkg_run", "w+") as last_check:
            last_check.write(current_date + "," + cur_master_commit + "," +
                             last_checkpkg_file)

        pkgs_list = []

        with open(last_checkpkg_file, "r") as csv:
            for line in csv:
                (pn, cur_ver, next_ver, maintainer) = self._parse_checkpkg_line(line)
                if (pn, cur_ver, next_ver, maintainer) != (None, None, None, None) and \
                        cur_ver != next_ver and next_ver != "N/A" and \
                        next_ver != "INVALID":
                    if self.pkg_upgradable(pn, next_ver, maintainer):
                        pkgs_list.append((pn, next_ver, maintainer))

        print("########### The list of packages to be upgraded ############")
        for p, v, m in pkgs_list:
            print("%s,%s,%s" % (p, v, m))
        print("############################################################")

        return pkgs_list

    def update_history(self, pn, new_ver, maintainer, upgrade_status):
        with open(self.history_file + ".tmp", "w+") as tmp_file:
            if os.path.exists(self.history_file):
                with open(self.history_file) as history:
                    for line in history:
                        if not line.startswith(pn):
                            tmp_file.write(line)
            tmp_file.write(pn + "," + new_ver + "," + maintainer +
                           "," + date.isoformat(date.today()) + "," +
                           upgrade_status + "\n")
        os.rename(self.history_file + ".tmp", self.history_file)

    # overriding the base method
    def pkg_upgrade_handler(self, err):
        if err is None:
            status_msg = "Succeeded"
        else:
            status_msg = str(err)

        status = type(err).__name__

        # drop last upgrade from git. It's safer this way if the upgrade has
        # problems and other packages depend on it. Give the other packages a
        # chance...
        if ("drop_previous_commits" in settings and
                settings["drop_previous_commits"] == "yes" and
                err is None) or (err is not None and self.patch_file is not None):
            I(" %s: Dropping changes from git ..." % self.pn)
            self.git.reset_hard(1)
            self.git.clean_untracked()

        self.update_history(self.pn, self.new_ver, self.maintainer,
                            status_msg)

        # don't bother maintainer with mails for unknown errors, unsuported
        # protocol or if the recipe is already up to date
        if status == "Error" or status == "UnsupportedProtocolError" or \
                status == "UpgradeNotNeededError":
            return

        if self.maintainer in maintainer_override:
            to_addr = maintainer_override[self.maintainer]
        else:
            to_addr = self.maintainer

        subject = "[AUH] " + self.pn + ": upgrading to " + self.new_ver
        if err is None:
            subject += " SUCCEEDED"
        else:
            subject += " FAILED"

        msg_body = self.mail_header % (self.pn, self.new_ver, status_msg)

        if err is None:
            msg_body += self.next_steps_info % os.path.basename(self.patch_file)

        msg_body += self.mail_footer

        attachments = []
        if self.patch_file is not None:
            attachments.append(self.patch_file)
        # License issue
        if status == "LicenseError":
            attachments.append(self.recipe.license_diff_file)
        elif err is not None:
            attachments.append(os.path.join(self.workdir, "bitbake_log.txt"))

        self.send_email(to_addr, subject, msg_body, attachments)

    def send_status_mail(self):
        if "status_recipients" not in settings:
            E("Could not send status email, no recipients set!")
            return -1

        to_list = settings["status_recipients"].split()

        subject = "[AUH] Upgrade status: " + date.isoformat(date.today())

        msg = self.statistics.pkg_stats() + self.statistics.maintainer_stats()

        if self.statistics.total_attempted:
            self.send_email(to_list, subject, msg)
        else:
            W("No packages attempted, not sending status mail!")

    def run(self):
        self.update_master()

        self.prepare()

        super(UniverseUpdater, self).run()

        self.send_status_mail()


if __name__ == "__main__":
    global settings
    global maintainer_override

    debug_levels = [log.CRITICAL, log.ERROR, log.WARNING, log.INFO, log.DEBUG]
    args = parse_cmdline()
    log.basicConfig(format='%(levelname)s:%(message)s',
                    level=debug_levels[args.debug_level - 1])

    settings, maintainer_override = parse_config_file(args.config_file)

    if len(args.package) == 1 and args.package[0] == "all":
        updater = UniverseUpdater()
        updater.run()
    elif len(args.package) >= 1:
        if len(args.package) == 1:
            pkg_list = [(args.package[0], args.to_version, None)]
        else:
            pkg_list = []
            for pkg in args.package:
                pkg_list.append((pkg, None, None))

        updater = Updater(args.auto_mode, args.skip_compilation)
        updater.run(pkg_list)
