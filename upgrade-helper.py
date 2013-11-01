#!/usr/bin/env python
# vim: set ts=4 sw=4 et:
#
# Copyright (c) 2013 Intel Corporation
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
#  This is a package upgrade helper script. Use 'upgrade-helper.py -h' for more
#  help.
#
# AUTHORS
# Laurentiu Palcu <laurentiu.palcu@intel.com>
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
    "   clean_tmp=yes\n"


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


class Git(object):
    def __init__(self, dir):
        self.repo_dir = dir
        super(Git, self).__init__()

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

    def clean_untracked(self):
        return self._cmd("clean -fd")

    def last_commit(self, branch_name):
        return self._cmd("log --pretty=format:\"%H\" -1" + branch_name)


class Bitbake(object):
    def __init__(self, build_dir):
        self.build_dir = build_dir
        super(Bitbake, self).__init__()

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
        msg_gen = Generator(out, mangle_from_=False).flatten(msg)
        msg_text = out.getvalue()

        try:
            smtp = SMTP(self.smtp_host, self.smtp_port)
            smtp.sendmail(self.from_addr, to_addr, msg_text)
            smtp.close()
        except Exception as e:
            E("Could not send email: %s" % str(e))
            return -1

        return 0


class Package(object):
    ERR_NONE = 0
    ERR_FETCH = ERR_NONE + 1
    ERR_PATCH = ERR_FETCH + 1
    ERR_RETRY = ERR_PATCH + 1
    ERR_CONFIGURE = ERR_RETRY + 1
    ERR_COMPILATION = ERR_CONFIGURE + 1
    ERR_LICENSE = ERR_COMPILATION + 1
    ERR_UPGRADE_NOT_NEEDED = ERR_LICENSE + 1
    ERR_UNSUPPORTED_PROTOCOL = ERR_UPGRADE_NOT_NEEDED + 1
    ERR_OTHER = ERR_UNSUPPORTED_PROTOCOL + 1

    def __init__(self, auto_mode):
        self.bb = Bitbake(get_build_dir())
        self.uh_dir = get_build_dir() + "/upgrade-helper"
        if not os.path.exists(self.uh_dir):
            os.mkdir(self.uh_dir)
        self.machines = ["qemux86", "qemux86-64", "qemuarm", "qemumips", "qemuppc"]
        self.interactive = not auto_mode

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
            (self.detect_git_repo, "Detecting git repository ...",
                                   None),
            (self.get_env, "Fetch old package environment ...",
                           "Could not fetch package environment!"),
            (self.get_next_version, None, "Could not get the next version!"),
            (self.check_upgrade_needed, None, "Already at latest version, upgrade not needed!"),
            (self.check_src_uri_protocol, None, "Unsupported SRC_URI protocol!"),
            (self.unpack_package, "Fetch/Unpack original package ...",
                                  "Fetching/Unpacking original package failed!"),
            (self.rename_files, "Renaming recipes, reset PR (if exists) ...",
                                "Rename operation failed"),
            (self.get_env, "Fetch new package environment ...",
                           "Could not fetch package environment!"),
            (self.cleanall_package, "Clean all ...",
                                    "Clean all failed!"),
            (self.fetch_package, "Fetch new package (old checksums) ...",
                                 "Fetching new package failed! Either it succeeded "
                                 "when it should've failed or it failed but no "
                                 "checksums were detected in the log."),
            (self.compile_package, None, "Compilation failed!")
        ]

        super(Package, self).__init__()

    def get_env(self):
        err, stdout, stderr = self.bb.env(self.pn)
        if err == -1:
            return None

        assignment = re.compile("^([^ \t=]*)=(.*)")
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
        self.recipe_dir = os.path.dirname(self.env['FILE'])
        return self.ERR_NONE

    def parse_checkpkg_line(self, line):
        m = re.match("^([^ \t]*)[ \t]+([^ \t]*)[ \t]+([^ \t]*).*<(.*)@(.*)>[ \t]+.*", line)
        if m is not None:
            return (m.group(1), m.group(2), m.group(3), m.group(4) + "@" + m.group(5))

        return (None, None, None, None)

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

            with open(get_build_dir() + "/tmp/log/checkpkg.csv", "r") as csv:
                (pn, cur_ver, self.to_ver, self.maintainer) = \
                    self.parse_checkpkg_line(csv.readlines()[1])

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
        # change PR before renaming
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
                            if line.startswith("PR=") or line.startswith("PR ="):
                                continue
                            else:
                                temp_recipe.write(line)
                os.rename(full_path_f + ".tmp", full_path_f)

        # rename recipes
        for path in os.listdir(self.recipe_dir):
            if path.find(self.env['PN']) == 0 and path.find(self.env['PKGV']) != -1:
                new_path = re.sub(self.env['PKGV'], self.to_ver, path)
                if self.git.mv(os.path.join(self.recipe_dir, path),
                               os.path.join(self.recipe_dir, new_path)) == (-1,):
                    return self.ERR_OTHER

        # since renaming was successful, save the old environment so it doesn't
        # get overwritten. It will be needed for license file diffs, etc.
        self.old_env = self.env

        return self.ERR_NONE

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

        return 0

    def handle_error_do_fetch(self, fetch_log):
        md5sum_line = None
        sha256sum_line = None

        with open(os.path.realpath(fetch_log)) as log:
            for line in log:
                m1 = re.match("^SRC_URI\[.*md5sum\].*", line)
                m2 = re.match("^SRC_URI\[.*sha256sum\].*", line)
                if m1 is not None:
                    md5sum_line = m1.group(0) + '\n'
                elif m2 is not None:
                    sha256sum_line = m2.group(0) + '\n'

        if md5sum_line is None or sha256sum_line is None:
            E(" %s: Fetch error, not checksum related!" % self.pn)
            return self.ERR_FETCH

        I(" %s: Update recipe checksums ..." % self.pn)
        # checksums are usually in the main recipe but they can also be in inc
        # files... Go through the recipes/inc files until we find them
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
                            m1 = re.match("^SRC_URI\[.*md5sum\].*", line)
                            m2 = re.match("^SRC_URI\[.*sha256sum\].*", line)
                            if m1 is not None:
                                temp_recipe.write(md5sum_line)
                            elif m2 is not None:
                                temp_recipe.write(sha256sum_line)
                            else:
                                temp_recipe.write(line)

                os.rename(full_path_f + ".tmp", full_path_f)

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
            dirs = [self.pn + "-" + self.env['PV'], self.pn, "files"]
            for dir in dirs:
                patch_file_path = os.path.join(self.recipe_dir, dir, patch_file)
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
                        os.rmdir(os.path.join(self.recipe_dir, dir))
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
                            return self.ERR_PATCH

            os.rename(self.env['FILE'] + ".tmp", self.env['FILE'])

            commit_msg += " * " + patch_file + "\n"

        # if we removed any backported patches, return 0, so we can
        # re-compile and see what happens
        if patches_removed:
            I(" %s: removed some backported patches, retrying ...", self.pn)
            self.commit_msg += commit_msg
            return self.ERR_RETRY

        return self.ERR_PATCH

    def handle_error_do_configure(self, config_log):
        license_file = None

        with open(config_log) as log:
            for line in log:
                if not line.startswith("ERROR:"):
                    continue

                m_old = re.match("ERROR: " + self.pn +
                                 ": md5 data is not matching for file://([^;]*);md5=(.*)$", line)
                m_old_lines = re.match("ERROR: " + self.pn +
                                       ": md5 data is not matching for file://([^;]*);beginline=[0-9]*;endline=[0-9]*;md5=(.*)$", line)
                m_new = re.match("ERROR: " + self.pn +
                                 ": The new md5 checksum is (.*)", line)
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
            self.license_diff_file = os.path.join(self.workdir, os.path.basename(license_file + ".diff"))
            if self.interactive:
                W("  %s: license checksum failed for file %s. The recipe has"
                  "been updated! View diff? (Y/n)" % (self.pn, license_file))
                answer = sys.stdin.readline().strip().upper()
                if answer == '' or answer == 'Y':
                    print(" ################ Licence file diff #################")
                    with open(self.license_diff_file) as diff:
                        print("%s" % diff.read())
                    print(" ####################################################")
                print("Retry compilation? (Y/n)")
                answer = sys.stdin.readline().strip().upper()
                if answer == '' or answer == 'Y':
                    return self.ERR_RETRY
            else:
                E(" %s: license checksum failed for file %s. "
                  "Updated recipe accordingly! Please check diff file: %s" %
                    (self.pn, license_file, self.license_diff_file))

            return self.ERR_LICENSE

        return self.ERR_CONFIGURE

    def handle_error_do_compile(self, compile_log):
        return self.ERR_COMPILATION

    def handle_error(self, err, stdout, stderr, expect_error=False):
        if expect_error and err == 0:
            return self.ERR_OTHER
        elif err == 0:
            return self.ERR_NONE

        failed = dict()
        machine = None

        for line in stdout.split("\n"):
            D(" %s: %s" % (self.pn, line))

            machine_match = re.match("MACHINE[\t ]+= *\"(.*)\"$", line)
            task_log_match = re.match("ERROR: Logfile of failure stored in: (.*/([^/]*)/[^/]*/temp/log\.(.*)\.[0-9]*)", line)
            incomp_host = re.match("ERROR: " + self.pn + " was skipped: incompatible with host (.*) \(.*$", line)

            if task_log_match is not None:
                failed[task_log_match.group(2)] = (task_log_match.group(3), task_log_match.group(1))
            elif machine_match is not None:
                machine = machine_match.group(1)
            elif incomp_host is not None:
                W(" %s: compilation failed: incompatible host %s" % (self.pn, incomp_host.group(1)))
                return self.ERR_NONE

        if len(failed) == 0:
            E(" %s: unable to extract failed task/recipe/log name from stdout!" % self.pn)
            return self.ERR_OTHER

        # if the failed recipe(s) is/are not the one we upgrade, try to clean sstate
        # and then retry
        if not self.pn in failed:
            already_retried = False
            for failed_recipe in failed:
                if failed_recipe in self.retried_recipes:
                    # we already retried, we'd best leave it to a human to handle
                    # it :)
                    already_retried = True
                # put the recipe in the retried list
                self.retried_recipes.add(failed_recipe)

            if already_retried:
                ret = self.ERR_OTHER
            else:
                I(" %s: The following recipe(s): %s, failed.  "
                  "Doing a 'cleansstate' and then retry ..." %
                  (self.pn, ' '.join(failed.keys())))
                self.bb.cleansstate(' '.join(failed.keys()))
                ret = self.ERR_RETRY
        else:
            if failed[self.pn][0] in self.error_handler:
                ret = self.error_handler[failed[self.pn][0]](failed[self.pn][1])
            else:
                ret = self.ERR_OTHER

        if ret != self.ERR_NONE and ret != self.ERR_RETRY:
            for failed_recipe in failed:
                W(" %s: %s(%s) failed, copying log to %s" %
                    (self.pn, failed_recipe,
                     failed[failed_recipe][0], self.workdir))
                os.symlink(failed[failed_recipe][1],
                           os.path.join(self.workdir, failed_recipe + "_" +
                                        machine + "_log." +
                                        failed[failed_recipe][0]))
            with open(os.path.join(self.workdir, "bitbake.log"), "w+") as log:
                log.write(stdout)

        return ret

    def create_workdir(self):
        self.workdir = self.uh_dir + "/" + self.pn

        if not os.path.exists(self.workdir):
            os.mkdir(self.workdir)
        else:
            for f in os.listdir(self.workdir):
                os.remove(os.path.join(self.workdir, f))

        return self.ERR_NONE

    def detect_git_repo(self):
        if self.get_env() != self.ERR_NONE:
            C(" %s: could not detect git repository!" % self.pn)
            exit(1)

        self.git = Git(self.recipe_dir)

        err, stdout, stderr = self.git.status()
        if err == -1:
            C(" %s: could not get repo status" % self.pn)
            exit(1)

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

        for machine in self.machines:
            retry = True
            while retry:
                retry = False
                I(" %s: compiling for %s ..." % (self.pn, machine))
                err = self.handle_error(*self.bb.complete(self.pn, machine))
                if err == self.ERR_NONE:
                    continue
                elif err == self.ERR_RETRY:
                    retry = True
                    continue
                else:
                    return err

        return self.ERR_NONE

    def upgrade(self, package_name, to_ver=None, maintainer=None, skip_compilation=False):
        self.pn = package_name
        self.checksums_changed = False
        self.maintainer = maintainer
        self.to_ver = to_ver
        self.skip_compilation = skip_compilation
        self.license_diff_file = None
        self.retried_recipes = set()

        for step, msg, err_msg in self.upgrade_steps:
            if msg is not None:
                I(" %s: %s" % (self.pn, msg))

            err = step()
            if err:
                E(" %s: %s" % (self.pn, err_msg))
                E(" %s: upgrade FAILED! Logs and/or file diffs are available in %s" % (self.pn, self.workdir))
                return err

        I(" %s: upgrade SUCCESSFUL! Commit and test!" % self.pn)

        return self.ERR_NONE


class Packages(Package):
    def __init__(self, auto_mode):
        super(Packages, self).__init__(auto_mode)
        self.patch_file = None
        self.author = None
        # a list of tuples (package, ver) for each upgrade status
        self.upgrade_stats = [
            ([], "Succeeded"),
            ([], "Failed(do_fetch)"),
            ([], "Failed(do_patch)"),
            ([], None),  # we'll never have entries here (compilation retry error code)
            ([], "Failed(do_configure)"),
            ([], "Failed(do_compile)"),
            ([], "Failed(license)"),
            ([], "Failed(upgrade not needed)"),
            ([], "Failed(SRC_URI protocol not supported)"),
            ([], "Failed(other errors)")
        ]
        self.succeeded = dict()
        self.failed = dict()

    def create_stat_msg(self):
        self.stat_msg = "Upgrade statistics:\n"
        self.stat_msg += "====================================================\n"
        total_attempted = self.succeeded["total"] + self.failed["total"]
        for l, msg in self.upgrade_stats:
            list_len = len(l)
            if list_len > 0:
                self.stat_msg += "* " + msg + ": " + str(list_len) + "\n"

                for pkg, to_ver, maintainer in l:
                    self.stat_msg += "    " + pkg + ", " + to_ver + ", " + \
                                     maintainer + "\n"

        self.stat_msg += "++++++++++++++++++++++++++++++++++++++++++++++++++++\n"
        self.stat_msg += "TOTAL: attempted=%d succeeded=%d(%.2f%%) failed=%d(%.2f%%)\n\n" % \
                         (total_attempted, self.succeeded["total"],
                         self.succeeded["total"] * 100.0 / total_attempted,
                         self.failed["total"],
                         self.failed["total"] * 100.0 / total_attempted)
        return 0

    def print_stats(self):
        print("%s" % self.stat_msg)
        return 0

    def update_statistics(self, err):
        self.upgrade_stats[err][0].append((self.pn, self.to_ver, self.maintainer))

        # add maintainer to the set of unique maintainers
        self.maintainers.add(self.maintainer)

        if not self.maintainer in self.succeeded:
            self.succeeded[self.maintainer] = 0
        if not self.maintainer in self.failed:
            self.failed[self.maintainer] = 0

        if err == self.ERR_NONE:
            self.succeeded["total"] += 1
            self.succeeded[self.maintainer] += 1
        else:
            self.failed["total"] += 1
            self.failed[self.maintainer] += 1

    # this function will be called at the end of each package upgrade
    def pkg_upgrade_handler(self, err):
        if err != self.ERR_NONE:
            I(" %s: Dropping changes from git ..." % self.pn)
            self.git.reset_hard(1)
            self.git.clean_untracked()
        return 0

    def upgrade(self, package_list):
        self.succeeded["total"] = 0
        self.failed["total"] = 0
        self.maintainers = set()
        total_pkgs = len(package_list)
        attempted_pkgs = 0
        for pn, to_ver, maintainer in package_list:
            attempted_pkgs += 1
            I(" ATTEMPT PACKAGE %d/%d" % (attempted_pkgs, total_pkgs))
            err = super(Packages, self).upgrade(pn, to_ver, maintainer)

            I(" %s: Auto commit changes ..." % self.pn)
            git_err, stdout, stderr = self.git.commit(self.commit_msg, self.author)
            if git_err == 0:
                I(" %s: Save patch in %s." % (self.pn, self.workdir))
                git_err, stdout, stderr = self.git.create_patch(self.workdir)
                if git_err == 0:
                    self.patch_file = stdout.strip()

            self.pkg_upgrade_handler(err)

            self.update_statistics(err)

        self.create_stat_msg()

        self.print_stats()


class Universe(Packages, Email):
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
        super(Universe, self).__init__(True)
        self.author = "Upgrade Helper <uh@not.set>"
        self.git = Git(os.path.dirname(os.getenv('PATH', False).split(':')[0]))

        # we don't need the detect_git_repo() step anymore, remove it
        self.upgrade_steps = [(f, s1, s2) for f, s1, s2 in self.upgrade_steps if f != self.detect_git_repo]

        # we don't need to get the next version for each package. We do it once
        # at the beginning
        self.upgrade_steps = [(f, s1, s2) for f, s1, s2 in self.upgrade_steps if f != self.get_next_version]

        # read history file
        self.history_file = os.path.join(get_build_dir(), "upgrade-helper", "history.uh")
        self.history = dict()
        if os.path.exists(self.history_file):
            with open(self.history_file) as history_file:
                for line in history_file:
                    self.history[line.split(',')[0]] = [line.split(',')[1], line.split(',')[2]]

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
        self.git.delete_branch("upgrades")
        I(" Sync master ...")
        self.git.pull()
        self.git.create_branch("upgrades")

        return 0

    def prepare(self):
        if "clean_sstate" in settings and settings["clean_sstate"] == "yes" and \
                os.path.exists(os.path.join(get_build_dir(), "sstate-cache")):
            I(" Removing sstate directory ...")
            shutil.rmtree(os.path.join(get_build_dir(), "sstate-cache"))
        if "clean_tmp" in settings and settings["clean_tmp"] == "yes" and \
                os.path.exists(os.path.join(get_build_dir(), "tmp")):
            I(" Removing tmp directory ...")
            shutil.rmtree(os.path.join(get_build_dir(), "tmp"))

        return 0

    def get_pkgs_to_upgrade(self):
        last_date_checked = None
        last_master_commit = None
        current_date = date.isoformat(date.today())
        err, stdout, stderr = self.git.last_commit("master")
        if err == 0:
            cur_master_commit = stdout
        else:
            cur_master_commit = "unknown"

        if os.path.exists(get_build_dir() + "/upgrade-helper/last_checkpkg_run"):
            with open(get_build_dir() + "/upgrade-helper/last_checkpkg_run") as last_check:
                line = last_check.read()
                last_date_checked = line.split(',')[0]
                last_master_commit = line.split(',')[1]
                last_checkpkg_file = line.split(',')[2]

        if last_master_commit != cur_master_commit or last_date_checked != current_date:
            I(" Fetching upstream versions for all packages ...")
            err = self.bb.checkpkg("universe")
            if err == -1:
                return self.ERR_OTHER

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
                (pn, cur_ver, next_ver, maintainer) = self.parse_checkpkg_line(line)
                if (pn, cur_ver, next_ver, maintainer) != (None, None, None, None) and \
                        cur_ver != next_ver and next_ver != "N/A" and \
                        next_ver != "INVALID":
                    if self.pkg_upgradable(pn, next_ver, maintainer):
                        pkgs_list.append((pn, next_ver, maintainer))

        return pkgs_list

    def update_history(self, pn, to_ver, maintainer, upgrade_status):
        with open(self.history_file + ".tmp", "w+") as tmp_file:
            if os.path.exists(self.history_file):
                with open(self.history_file) as history:
                    for line in history:
                        if not line.startswith(pn):
                            tmp_file.write(line)
            tmp_file.write(pn + "," + to_ver + "," + maintainer +
                           "," + upgrade_status + "\n")
        os.rename(self.history_file + ".tmp", self.history_file)

    # overriding the base method
    def pkg_upgrade_handler(self, err):
        # drop last upgrade from git. It's safer this way if the upgrade has
        # problems and other packages depend on it. Give the other packages a
        # chance...
        I(" %s: Dropping changes from git ..." % self.pn)
        self.git.reset_hard(1)
        self.git.clean_untracked()

        # do not update history if error was ERR_OTHER or ERR_FETCH. The
        # former may mean that other recipes failed and the latter that there
        # was a transient network problem. Hence, we can try them next time
        if err != self.ERR_OTHER and err != self.ERR_FETCH:
            self.update_history(self.pn, self.to_ver, self.maintainer,
                                self.upgrade_stats[err][1])

        # don't bother maintainer with mails for these errors
        if err == self.ERR_UNSUPPORTED_PROTOCOL or err == self.ERR_OTHER:
            return 0

        if self.maintainer in maintainer_override:
            to_addr = maintainer_override[self.maintainer]
        else:
            to_addr = self.maintainer

        subject = "[AUH] " + self.pn + ": upgrading to " + self.to_ver
        if err == self.ERR_NONE:
            subject += " SUCCEEDED"
        else:
            subject += " FAILED"

        msg_body = self.mail_header % \
            (self.pn, self.to_ver, self.upgrade_stats[err][1])

        if err == self.ERR_NONE:
            msg_body += self.next_steps_info % os.path.basename(self.patch_file)

        msg_body += self.mail_footer

        attachments = []
        if self.patch_file is not None:
            attachments.append(self.patch_file)
        if err == self.ERR_LICENSE:
            attachments.append(self.license_diff_file)
        elif err != self.ERR_NONE:
            attachments.append(os.path.join(self.workdir, "bitbake.log"))

        self.send_email(to_addr, subject, msg_body, attachments)

        return 0

    def create_stat_msg(self):
        super(Universe, self).create_stat_msg()
        self.stat_msg += "* Statistics per maintainer:\n"
        for m in self.maintainers:
            total_attempted = self.succeeded[m] + self.failed[m]
            self.stat_msg += "    %s: attempted=%d succeeded=%d(%.2f%%) failed=%d(%.2f%%)\n\n" % \
                             (m.split("@")[0], total_attempted, self.succeeded[m],
                             self.succeeded[m] * 100.0 / total_attempted,
                             self.failed[m],
                             self.failed[m] * 100.0 / total_attempted)

        return 0

    def send_status_mail(self):
        if "status_recipients" not in settings:
            E("Could not send status email, no recipients set!")
            return -1

        to_list = settings["status_recipients"].split()

        subject = "[AUH] Upgrade status: " + date.isoformat(date.today())

        self.send_email(to_list, subject, self.stat_msg)
        return 0

    def upgrade(self):
        self.update_master()

        self.prepare()

        package_list = self.get_pkgs_to_upgrade()

        print("########### The list of packages to be upgraded ############")
        for p, v, m in package_list:
            print("%s,%s,%s" % (p, v, m))
        print("############################################################")

        super(Universe, self).upgrade(package_list)

        self.send_status_mail()

        return 0


if __name__ == "__main__":
    global settings
    global maintainer_override

    debug_levels = [log.CRITICAL, log.ERROR, log.WARNING, log.INFO, log.DEBUG]
    args = parse_cmdline()
    log.basicConfig(format='%(levelname)s:%(message)s',
                    level=debug_levels[args.debug_level - 1])

    settings, maintainer_override = parse_config_file(args.config_file)

    if len(args.package) > 1:
        pkg_list = []
        for pkg in args.package:
            pkg_list.append((pkg, None, None))
        pkgs = Packages(args.auto_mode)
        pkgs.upgrade(pkg_list)
    else:
        if args.package[0] == "all":
            universe = Universe()
            universe.upgrade()
        else:
            pkg = Package(args.auto_mode)
            pkg.upgrade(args.package[0], args.to_version, args.skip_compilation)
