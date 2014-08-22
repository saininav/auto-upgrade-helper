#!/usr/bin/env python
# vim: set ts=4 sw=4 et:
#
# Copyright (c) 2013 - 2014 Intel Corporation
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
# DESCRIPTION
#  This is a recipe upgrade helper script for the Yocto Project.
#  Use 'upgrade-helper.py -h' for more help.
#
# AUTHORS
# Laurentiu Palcu   <laurentiu.palcu@intel.com>
# Marius Avram      <marius.avram@intel.com>
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
import signal
import sys
import ConfigParser as cp
from datetime import datetime
from datetime import date
import shutil
from errors import *
from git import Git
from bitbake import Bitbake, BuildHistory
from emailhandler import Email
from statistics import Statistics
from recipe import Recipe
from gitrecipe import GitRecipe
from svnrecipe import SvnRecipe

help_text = """Usage examples:
* To upgrade xmodmap recipe to the latest available version, interactively:
    $ upgrade-helper.py xmodmap

* To attempt to upgrade all recipes and automatically send email messages
  to maintainers for each attempted recipe as well as a status mail at the
  end, use:
    $ upgrade-helper.py all
"""


def parse_cmdline():
    parser = argparse.ArgumentParser(description='Package Upgrade Helper',
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     epilog=help_text)
    parser.add_argument("recipe", nargs="+", help="recipe to be upgraded")
    parser.add_argument("-t", "--to_version",
                        help="version to upgrade the recipe to")
    parser.add_argument("-a", "--auto-mode", action="store_true", default=False,
                        help="disable interactive mode")
    parser.add_argument("-d", "--debug-level", type=int, default=4, choices=range(1, 6),
                        help="set the debug level: CRITICAL=1, ERROR=2, WARNING=3, INFO=4, DEBUG=5")
    parser.add_argument("-e", "--send-emails", action="store_true", default=False,
                        help="send emails to recipe maintainers")
    parser.add_argument("-s", "--skip-compilation", action="store_true", default=False,
                        help="do not compile, just change the checksums, remove PR, and commit")
    parser.add_argument("-c", "--config-file", default=None,
                        help="Path to the configuration file. Default is $BUILDDIR/upgrade-helper/upgrade-helper.conf")
    return parser.parse_args()


def get_build_dir():
    return os.getenv('BUILDDIR')


def parse_config_file(config_file):
    settings = dict()
    maintainer_override = dict()

    if config_file:
        if os.path.exists(config_file):
            cfg_file = config_file
        else:
            C("Unable to find specified config file %s" % config_file)
            sys.exit(1)
    else:
        cfg_file = os.path.join(get_build_dir(), "upgrade-helper", "upgrade-helper.conf")

    if os.path.exists(cfg_file):
        D("Reading config file %s" % cfg_file)
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

class Updater(object):
    mail_header = \
        "Hello,\n\nYou are receiving this email because you are the maintainer\n" \
        "of *%s* recipe and this is to let you know that the automatic attempt\n" \
        "to upgrade the recipe to *%s* has %s.\n\n"

    next_steps_info = \
        "The recipe has been successfully compiled for all major architectures.\n\n" \
        "Next steps:\n" \
        "    - apply the patch: git am %s\n" \
        "    - check that required patches have not been removed from the recipe\n" \
        "    - compile an image that contains the package\n" \
        "    - perform some basic sanity tests\n" \
        "    - amend the patch and sign it off: git commit -s --reset-author --amend\n" \
        "    - send it to the list\n\n" \

    mail_footer = \
        "Attached are the patch and the logs (+ license file diff) in case of failure.\n\n" \
        "Regards,\nThe Upgrade Helper"


    def __init__(self, auto_mode=False, send_email=False, skip_compilation=False):

        self.uh_dir = get_build_dir() + "/upgrade-helper"
        if not os.path.exists(self.uh_dir):
            os.mkdir(self.uh_dir)

        self.bb = Bitbake(get_build_dir())
        self.buildhistory = BuildHistory(get_build_dir())
        self.git = None
        if send_email:
            self.author = "Upgrade Helper <uh@not.set>"
        else:
            self.author = None
        self.skip_compilation = skip_compilation
        self.interactive = not auto_mode
        self.send_email = send_email

        self.machines = settings.get('machines', 'qemux86 qemux86-64 qemuarm qemumips qemuppc').split()

        self.upgrade_steps = [
            (self._create_workdir, None),
            (self._detect_repo, "Detecting git repository location ..."),
            (self._clean_repo, "Cleaning git repository of temporary branch ..."),
            (self._detect_recipe_type, None),
            (self._unpack_original, "Fetch & unpack original version ..."),
            (self._rename, "Renaming recipes, reset PR (if exists) ..."),
            (self._cleanall, "Clean all ..."),
            (self._fetch, "Fetch new version (old checksums) ..."),
            (self._compile, None)
        ]

        self.email_handler = Email(settings)
        self.statistics = Statistics()



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
        m = re.match("^([^ \t]*)[ \t]+([^ \t]*)[ \t]+([^ \t]*)[ \t]+.*", line)
        if m:
            res = (m.group(1), m.group(2), m.group(3))
            m = re.search("<([^ \t]+@[^ \t]+)>", line)
            if m:
                maintainer = m.group(1)
            else:
                maintainer = None
            return res + (maintainer,)

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

    def _clean_repo(self):
        try:
            self.git.checkout_branch("upgrades")
        except Error:
            self.git.create_branch("upgrades")
        try:
            self.git.delete_branch("remove_patches")
        except:
            pass

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

    def _review(self):
        # Check build_history
        if not self.skip_compilation:
            I(" %s: Checking buildhistory ..." % self.pn)
            self.buildhistory.set_work_dir(self.workdir)
            if self.buildhistory.diff(len(self.machines)):
               I(" %s: Wrote buildhistory-diff output ..." % self.pn)

    def _check_upstream_versions(self, packages=[("universe", None, None)]):
        I(" Fetching upstream version(s) ...")

        try:
            self.bb.checkpkg(" ".join([p[0] for p in packages]))
        except Error as e:
            for line in e.stdout.split('\n'):
                if line.find("ERROR: Task do_checkpkg does not exist") == 0:
                    C(" \"distrodata.bbclass\" not inherited. Consider adding "
                      "the following to your local.conf:\n\n"
                      "INHERIT =+ \"distrodata\"\n")
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
            # Skip header line
            next(csv)
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

    # this function will be called at the end of each recipe upgrade
    def pkg_upgrade_handler(self, err):
        if err and self.patch_file:
            answer = "N"
            status_msg = str(err)
            if self.interactive:
                I(" %s: Do you want to keep the changes? (y/N)" % self.pn)
                answer = sys.stdin.readline().strip().upper()

            if answer == '' or answer == 'N':
                I(" %s: Dropping changes from git ..." % self.pn)
                self.git.reset_hard(1)
                self.git.clean_untracked()
                return
        elif not err:
            status_msg = "Succeeded"

        status = type(err).__name__

        # drop last upgrade from git. It's safer this way if the upgrade has
        # problems and other recipes depend on it. Give the other recipes a
        # chance...
        if (settings.get("drop_previous_commits", "no") == "yes" and
                not err) or (err and self.patch_file):
            I(" %s: Dropping changes from git ..." % self.pn)
            self.git.reset_hard(1)
            self.git.clean_untracked()

        if self.send_email:
            # don't bother maintainer with mail if the recipe is already up to date
            if status == "UpgradeNotNeededError":
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

            # Add possible attachments to email
            attachments = []
            for attachment in os.listdir(self.workdir):
                attachment_fullpath = os.path.join(self.workdir, attachment)
                if os.path.isfile(attachment_fullpath):
                    attachments.append(attachment_fullpath)

            self.email_handler.send_email(to_addr, subject, msg_body, attachments)

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

    def send_status_mail(self):
        if "status_recipients" not in settings:
            E("Could not send status email, no recipients set!")
            return -1

        to_list = settings["status_recipients"].split()

        subject = "[AUH] Upgrade status: " + date.isoformat(date.today())

        msg = self.statistics.pkg_stats() + self.statistics.maintainer_stats()

        if self.statistics.total_attempted:
            self.email_handler.send_email(to_list, subject, msg)
        else:
            W("No recipes attempted, not sending status mail!")

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
                I(" %s: Upgrading to %s" % (self.pn, self.new_ver))
                for step, msg in self.upgrade_steps:
                    if msg is not None:
                        I(" %s: %s" % (self.pn, msg))
                    step()

                I(" %s: Upgrade SUCCESSFUL! Please test!" % self.pn)
                error = None
            except UpgradeNotNeededError as e:
                I(" %s: %s" % (self.pn, e.message))
                error = e
            except Error as e:
                E(" %s: %s" % (self.pn, e.message))
                E(" %s: Upgrade FAILED! Logs and/or file diffs are available in %s" % (self.pn, self.workdir))
                error = e

            self._commit_changes()

            self.pkg_upgrade_handler(error)

            self.statistics.update(self.pn, self.new_ver, self.maintainer, error)

            if self.interactive and attempted_pkgs < total_pkgs:
                I(" %s: Proceed to next recipe? (Y/n)" % self.pn)
                answer = sys.stdin.readline().strip().upper()

                if answer != 'Y' and answer != '':
                    I("Aborted by user!")
                    exit(0)

        if (attempted_pkgs > 1):
            print("%s" % self.statistics.pkg_stats())
            if self.send_email:
                self.send_status_mail()

class UniverseUpdater(Updater):
    def __init__(self):
        Updater.__init__(self, True, True)
        self.git = Git(os.path.dirname(os.getenv('PATH', False).split(':')[0]))

        # read history file
        self.history_file = os.path.join(get_build_dir(), "upgrade-helper", "history.uh")
        self.history = dict()
        if os.path.exists(self.history_file):
            with open(self.history_file) as history_file:
                for line in history_file:
                    line = line.strip()
                    self.history[line.split(',')[0]] = [line.split(',')[1],
                                                        line.split(',')[2],
                                                        line.split(',')[3],
                                                        line.split(',')[4]]

    # checks if maintainer is in whitelist and that the recipe itself is not
    # blacklisted: python, gcc, etc. Also, check the history if the recipe
    # hasn't already been tried
    def pkg_upgradable(self, pn, next_ver, maintainer):
        if not maintainer:
            D("Skipping upgrade of %s: no maintainer" % pn)
            return False

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
                # retry recipes that had fetch errors or other errors after
                # more than 7 days
                if (self.history[pn][3] == str(FetchError()) or
                        self.history[pn][3] == str(Error())) and retry_delta > 7:
                    return True

                return False

        # drop native/cross/cross-canadian recipes. We deal with native
        # when upgrading the main recipe but we keep away of cross* pkgs...
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
        if settings.get("clean_sstate", "no") == "yes" and \
                os.path.exists(os.path.join(get_build_dir(), "sstate-cache")):
            I(" Removing sstate directory ...")
            shutil.rmtree(os.path.join(get_build_dir(), "sstate-cache"))
        if settings.get("clean_tmp", "no") == "yes" and \
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

        pkgs_list = []

        with open(last_checkpkg_file, "r") as csv:
            for line in csv:
                (pn, cur_ver, next_ver, maintainer) = self._parse_checkpkg_line(line)
                if (pn, cur_ver, next_ver, maintainer) != (None, None, None, None) and \
                        cur_ver != next_ver and next_ver != "N/A" and \
                        next_ver != "INVALID":
                    if self.pkg_upgradable(pn, next_ver, maintainer):
                        pkgs_list.append((pn, next_ver, maintainer))

        # Update last_checkpkg_run only after the version check has been completed
        with open(get_build_dir() + "/upgrade-helper/last_checkpkg_run", "w+") as last_check:
            last_check.write(current_date + "," + cur_master_commit + "," +
                             last_checkpkg_file)


        print("########### The list of recipes to be upgraded ############")
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
        super(UniverseUpdater, self).pkg_upgrade_handler(self)
        self.update_history(self.pn, self.new_ver, self.maintainer, status_msg)

    def run(self):
        self.update_master()
        self.prepare()
        super(UniverseUpdater, self).run()

def close_child_processes(signal_id, frame):
    pid = os.getpgrp()
    os.killpg(pid, signal.SIGKILL)

if __name__ == "__main__":
    global settings
    global maintainer_override

    signal.signal(signal.SIGINT, close_child_processes)

    debug_levels = [log.CRITICAL, log.ERROR, log.WARNING, log.INFO, log.DEBUG]
    args = parse_cmdline()
    log.basicConfig(format='%(levelname)s:%(message)s',
                    level=debug_levels[args.debug_level - 1])

    if not os.getenv('BUILDDIR', False):
        E(" You must source oe-init-build-env before running this script!\n")
        exit(1)

    settings, maintainer_override = parse_config_file(args.config_file)

    if len(args.recipe) == 1 and args.recipe[0] == "all":
        updater = UniverseUpdater()
        updater.run()
    elif len(args.recipe) >= 1:
        if len(args.recipe) == 1:
            pkg_list = [(args.recipe[0], args.to_version, None)]
        else:
            pkg_list = []
            for pkg in args.recipe:
                pkg_list.append((pkg, None, None))

        updater = Updater(args.auto_mode, args.send_emails, args.skip_compilation)
        updater.run(pkg_list)
