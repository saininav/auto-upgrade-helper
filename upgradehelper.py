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
import subprocess

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

sys.path.insert(1, os.path.join(os.path.abspath(
    os.path.dirname(__file__)), 'modules'))

from errors import *

from utils.git import Git
from utils.bitbake import Bitbake
from utils.emailhandler import Email

from statistics import Statistics
from steps import upgrade_steps
from testimage import TestImage

help_text = """Usage examples:
* To upgrade xmodmap recipe to the latest available version, interactively:
    $ upgrade-helper.py xmodmap

* To attempt to upgrade all recipes and automatically send email messages
  to maintainers for each attempted recipe as well as a status mail at the
  end, use:
    $ upgrade-helper.py all
"""

DEFAULT_TESTIMAGE = 'core-image-sato'

def parse_cmdline():
    parser = argparse.ArgumentParser(description='Package Upgrade Helper',
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     epilog=help_text)
    parser.add_argument("recipe", help="recipe to be upgraded")

    parser.add_argument("-t", "--to_version",
                        help="version to upgrade the recipe to")
    parser.add_argument("-m", "--maintainer",
                        help="maintainer of the recipe")

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
    def __init__(self, auto_mode=False, send_email=False, skip_compilation=False):
        build_dir = get_build_dir()

        self._make_dirs(build_dir)

        self._add_file_logger()

        self.bb = Bitbake(build_dir)

        try:
            self.base_env = self.bb.env()
        except EmptyEnvError as e:
            import traceback
            E( " %s\n%s" % (e.message, traceback.format_exc()))
            E( " Bitbake output:\n%s" % (e.stdout))
            exit(1)

        self.email_handler = Email(settings)
        self.statistics = Statistics()
        # XXX: assume that the poky directory is the first entry in the PATH
        self.git = Git(os.path.dirname(os.getenv('PATH', False).split(':')[0]))

        self.opts = {}
        self.opts['interactive'] = not auto_mode
        self.opts['send_email'] = send_email
        self.opts['author'] = "Upgrade Helper <%s>" % \
                settings.get('from', 'uh@not.set')
        self.opts['machines'] = settings.get('machines',
                'qemux86 qemux86-64 qemuarm qemumips qemuppc').split()
        self.opts['skip_compilation'] = skip_compilation
        self.opts['buildhistory'] = self._buildhistory_is_enabled()
        self.opts['testimage'] = self._testimage_is_enabled()

    def _make_dirs(self, build_dir):
        self.uh_dir = os.path.join(build_dir, "upgrade-helper")
        if not os.path.exists(self.uh_dir):
            os.mkdir(self.uh_dir)
        uh_base_work_dir = settings.get('workdir', '')
        if not uh_base_work_dir:
            uh_base_work_dir = self.uh_dir
        self.uh_work_dir = os.path.join(uh_base_work_dir, "%s" % \
                datetime.now().strftime("%Y%m%d%H%M%S"))
        os.mkdir(self.uh_work_dir)
        self.uh_recipes_all_dir = os.path.join(self.uh_work_dir, "all")
        os.mkdir(self.uh_recipes_all_dir)
        self.uh_recipes_succeed_dir = os.path.join(self.uh_work_dir, "succeed")
        os.mkdir(self.uh_recipes_succeed_dir)
        self.uh_recipes_failed_dir = os.path.join(self.uh_work_dir, "failed")
        os.mkdir(self.uh_recipes_failed_dir)

    def _add_file_logger(self):
        fh = log.FileHandler(os.path.join(self.uh_work_dir, "upgrade-helper.log"))
        logger = log.getLogger()
        logger.addHandler(fh)

    def _get_status_msg(self, err):
        if err:
            return str(err)
        else:
            return "Succeeded"

    def _buildhistory_is_enabled(self):
        enabled = False

        if settings.get("buildhistory", "no") == "yes":
            if 'buildhistory' in self.base_env['INHERIT']:
                if not 'BUILDHISTORY_COMMIT' in self.base_env:
                    E(" Buildhistory was INHERIT in conf/local.conf"\
                      " but need BUILDHISTORY_COMMIT=1 please set.")
                    exit(1)

                if not self.base_env['BUILDHISTORY_COMMIT'] == '1':
                    E(" Buildhistory was INHERIT in conf/local.conf"\
                      " but need BUILDHISTORY_COMMIT=1 please set.")
                    exit(1)

                if self.opts['skip_compilation']:
                    W(" Buildhistory disabled because user" \
                            " skip compilation!")
                else:
                    enabled = True
            else:
                E(" Buildhistory was enabled in upgrade-helper.conf"\
                  " but isn't INHERIT in conf/local.conf, if you want"\
                  " to enable please set.")
                exit(1)
        else:
            if 'buildhistory' in self.base_env['INHERIT']:
                E(" Buildhistory was INHERIT in conf/local.conf"\
                  " but buildhistory=yes isn't in upgrade-helper.conf,"\
                  " if you want to enable please set.")
                exit(1)

        return enabled

    def _testimage_is_enabled(self):
        enabled = False

        if settings.get("testimage", "no") == "yes":
            if 'testimage' in self.base_env['INHERIT']:
                if not "ptest" in self.base_env["DISTRO_FEATURES"]:
                    E(" testimage requires ptest in DISTRO_FEATURES please add to"\
                      " conf/local.conf.")
                    exit(1)

                if not "package-management" in self.base_env['EXTRA_IMAGE_FEATURES']:
                    E(" testimage requires package-management in EXTRA_IMAGE_FEATURES"\
                      " please add to conf/local.conf.")
                    exit(1)

                if not "package_rpm" == self.base_env["PACKAGE_CLASSES"]:
                    E(" testimage/ptest requires PACKAGE_CLASSES set to package_rpm"\
                      " please add to conf/local.conf.")
                    exit(1)

                enabled = True
            else:
                E(" testimage was enabled in upgrade-helper.conf"\
                  " but isn't INHERIT in conf/local.conf, if you want"\
                  " to enable please set.")
                exit(1)
        else:
            if 'testimage' in self.base_env['INHERIT']:
                E(" testimage was INHERIT in conf/local.conf"\
                  " but testimage=yes isn't in upgrade-helper.conf,"\
                  " if you want to enable please set.")
                exit(1)

        return enabled

    def _get_packages_to_upgrade(self, packages=None):
        if packages is None:
            I( "Nothing to upgrade")
            exit(0)
        else:
            return packages

    # this function will be called at the end of each recipe upgrade
    def pkg_upgrade_handler(self, pkg_ctx):
        if self.opts['interactive'] and pkg_ctx['error'] and pkg_ctx['patch_file']:
            answer = "N"
            I(" %s: Do you want to keep the changes? (y/N)" % pkg_ctx['PN'])
            answer = sys.stdin.readline().strip().upper()

            if answer == '' or answer == 'N':
                I(" %s: Dropping changes from git ..." % pkg_ctx['PN'])
                self.git.reset_hard(1)
                self.git.clean_untracked()
                return

        # drop last upgrade from git. It's safer this way if the upgrade has
        # problems and other recipes depend on it. Give the other recipes a
        # chance...
        if (settings.get("drop_previous_commits", "no") == "yes" and
                not pkg_ctx['error']) or (pkg_ctx['error'] and pkg_ctx['patch_file']):
            I(" %s: Dropping changes from git ..." % pkg_ctx['PN'])
            self.git.reset_hard(1)
            self.git.clean_untracked()

        mail_header = \
            "Hello,\n\nYou are receiving this email because you are the maintainer\n" \
            "of *%s* recipe and this is to let you know that the automatic attempt\n" \
            "to upgrade the recipe to *%s* has %s.\n\n"

        license_change_info = \
            "*LICENSE CHANGED* please review the %s file and update the LICENSE\n" \
            "variable in the recipe if is needed.\n\n"

        next_steps_info = \
            "The recipe has been successfully compiled for machines %s.\n\n" \
            "Next steps:\n" \
            "    - apply the patch: git am %s\n" \
            "    - check that required upstream patches have not been removed from the recipe,\n" \
            "      if upstream patches were removed the reason is specified in the commit message.\n" \
            "    - compile an image that contains the package\n" \
            "    - perform some basic sanity tests\n" \
            "    - amend the patch and sign it off: git commit -s --reset-author --amend\n" \
            "    - send it to the list\n\n" \

        testimage_ptest_info = \
            "The recipe has ptest enabled and has been tested with core-image-minimal/ptest \n" \
            "with the next machines %s. Attached is the log file.\n\n"

        testimage_info = \
            "The recipe has been tested using %s testimage and succeeded with \n" \
            "the next machines %s. Attached is the log file.\n\n" \

        mail_footer = \
            "Attached are the patch, license diff (if change) and bitbake log.\n" \
            "Any problem please contact Anibal Limon <anibal.limon@intel.com>.\n\n" \
            "Regards,\nThe Upgrade Helper"

        if pkg_ctx['MAINTAINER'] in maintainer_override:
            to_addr = maintainer_override[pkg_ctx['MAINTAINER']]
        else:
            to_addr = pkg_ctx['MAINTAINER']

        cc_addr = None
        if "status_recipients" in settings:
            cc_addr = settings["status_recipients"].split()

        subject = "[AUH] " + pkg_ctx['PN'] + ": upgrading to " + pkg_ctx['NPV']
        if not pkg_ctx['error']:
            subject += " SUCCEEDED"
        else:
            subject += " FAILED"
        msg_body = mail_header % (pkg_ctx['PN'], pkg_ctx['NPV'],
                self._get_status_msg(pkg_ctx['error']))
        if 'recipe' in pkg_ctx:
            license_diff_fn = pkg_ctx['recipe'].get_license_diff_file_name()
            if license_diff_fn:
                msg_body += license_change_info % license_diff_fn
        if not pkg_ctx['error']:
            msg_body += next_steps_info % (', '.join(self.opts['machines']),
                    os.path.basename(pkg_ctx['patch_file']))

        if self.opts['testimage']:
            if 'ptest' in pkg_ctx:
                machines = pkg_ctx['ptest'].keys()
                msg_body += testimage_ptest_info % machines
            if 'testimage' in pkg_ctx:
                machines = pkg_ctx['testimage'].keys()
                msg_body += testimage_info % (settings.get('testimage_name', \
                    DEFAULT_TESTIMAGE), machines)

        msg_body += mail_footer

        # Add possible attachments to email
        attachments = []
        for attachment in os.listdir(pkg_ctx['workdir']):
            attachment_fullpath = os.path.join(pkg_ctx['workdir'], attachment)
            if os.path.isfile(attachment_fullpath):
                attachments.append(attachment_fullpath)

        # Only send email to Maintainer when recipe upgrade succeed.
        if self.opts['send_email'] and not pkg_ctx['error']:
            self.email_handler.send_email(to_addr, subject, msg_body, attachments, cc_addr=cc_addr)
        # Preserve email for review purposes.
        email_file = os.path.join(pkg_ctx['workdir'],
                    "email_summary")
        with open(email_file, "w+") as f:
            f.write("To: %s\n" % to_addr)
            if isinstance(cc_addr, list):
                f.write("To: %s\n" % ' '.join(to_addr))
            else:
                f.write("Cc: %s\n" % to_addr)

            f.write("Subject: %s\n" % subject)
            f.write("Attachments: %s\n" % ' '.join(attachments))
            f.write("\n%s\n" % msg_body)

    def commit_changes(self, pkg_ctx):
        try:
            pkg_ctx['patch_file'] = None

            if 'recipe' in pkg_ctx:
                I(" %s: Auto commit changes ..." % pkg_ctx['PN'])
                self.git.commit(pkg_ctx['recipe'].commit_msg, self.opts['author'])

                I(" %s: Save patch in directory: %s." %
                        (pkg_ctx['PN'], pkg_ctx['workdir']))

                stdout = self.git.create_patch(pkg_ctx['workdir'])
                pkg_ctx['patch_file'] = stdout.strip()
        except Error as e:
            for line in e.stdout.split("\n"):
                if line.find("nothing to commit") == 0:
                    I(" %s: Nothing to commit!" % pkg_ctx['PN'])
                    return

            I(" %s: %s" % (pkg_ctx['PN'], e.stdout))
            raise e

    def send_status_mail(self, statistics_summary):
        if "status_recipients" not in settings:
            E(" Could not send status email, no recipients set!")
            return -1

        to_list = settings["status_recipients"].split()

        subject = "[AUH] Upgrade status: " + date.isoformat(date.today())

        if self.statistics.total_attempted:
            self.email_handler.send_email(to_list, subject, statistics_summary)
        else:
            W("No recipes attempted, not sending status mail!")

    def _order_pkgs_to_upgrade(self, pkgs_to_upgrade):
        def _get_pn_dep_dic(pn_list, dependency_file): 
            import re

            pn_dep_dic = {}

            with open(dependency_file) as dep:
                data = dep.read()
                dep.close()

                for line in data.split('\n'):
                    m = re.search('^"(.*)" -> "(.*)"$', line)
                    if not m:
                        continue

                    pn = m.group(1)
                    pn_dep = m.group(2)
                    if pn == pn_dep:
                        continue

                    if pn in pn_list:
                        if pn_dep in pn_list:
                            if pn in pn_dep_dic.keys():
                                pn_dep_dic[pn].append(pn_dep)
                            else:
                                pn_dep_dic[pn] = [pn_dep]
                        elif not pn in pn_dep_dic.keys():
                            pn_dep_dic[pn] = []

            return pn_dep_dic

        def _dep_resolve(graph, node, resolved, seen):
            seen.append(node)

            for edge in graph[node]:
                if edge not in resolved:
                    if edge in seen:
                        raise RuntimeError("Packages %s and %s have " \
                                "a circular dependency." \
                                % (node, edge))
                    _dep_resolve(graph, edge, resolved, seen)

            resolved.append(node)


        pn_list = []
        for pn, new_ver, maintainer in pkgs_to_upgrade:
            pn_list.append(pn)

        try:
           self.bb.dependency_graph(' '.join(pn_list))
        except Error as e:
            multiple_providers = False
            for l in e.stdout.split('\n'):
                if l.find("ERROR: Multiple .bb files are due to be built which each provide") == 0:
                    multiple_providers = True
            if not multiple_providers:
                raise e

        dependency_file = os.path.join(get_build_dir(), "pn-depends.dot")

        pkgs_to_upgrade_ordered = []
        pn_list_ordered = []

        pn_dep_dic = _get_pn_dep_dic(pn_list, dependency_file)
        if pn_dep_dic:
            root = "__root_node__"
            pn_dep_dic[root] = pn_dep_dic.keys()
            _dep_resolve(pn_dep_dic, root, pn_list_ordered, [])
            pn_list_ordered.remove(root)

        for pn_ordered in pn_list_ordered:
            for pn, new_ver, maintainer in pkgs_to_upgrade:
                if pn == pn_ordered: 
                    pkgs_to_upgrade_ordered.append([pn, new_ver, maintainer])

        return pkgs_to_upgrade_ordered

    def run(self, package_list=None):
        I(" Building gcc runtimes ...")
        for machine in self.opts['machines']:
            I("  building gcc runtime for %s" % machine)
            try:
                self.bb.complete("gcc-runtime", machine)
            except Exception as e:
                E(" Can't build gcc-runtime for %s." % machine)

                if isinstance(e, Error):
                    E(e.stdout)
                else:
                    import traceback
                    traceback.print_exc(file=sys.stdout)

        pkgs_to_upgrade = self._order_pkgs_to_upgrade(
                self._get_packages_to_upgrade(package_list))
        total_pkgs = len(pkgs_to_upgrade)

        pkgs_ctx = {}

        I(" ########### The list of recipes to be upgraded #############")
        for p, v, m in pkgs_to_upgrade:
            I(" %s, %s, %s" % (p, v, m))

            pkgs_ctx[p] = {}
            pkgs_ctx[p]['PN'] = p
            pkgs_ctx[p]['NPV'] = v
            pkgs_ctx[p]['MAINTAINER'] = m

            pkgs_ctx[p]['base_dir'] = self.uh_recipes_all_dir
        I(" ############################################################")

        succeeded_pkgs_ctx = []
        failed_pkgs_ctx = []
        attempted_pkgs = 0
        for pn, _, _ in pkgs_to_upgrade:
            pkg_ctx = pkgs_ctx[pn]
            pkg_ctx['error'] = None

            attempted_pkgs += 1
            I(" ATTEMPT PACKAGE %d/%d" % (attempted_pkgs, total_pkgs))
            try:
                I(" %s: Upgrading to %s" % (pkg_ctx['PN'], pkg_ctx['NPV']))
                for step, msg in upgrade_steps:
                    if msg is not None:
                        I(" %s: %s" % (pkg_ctx['PN'], msg))
                    step(self.bb, self.git, self.opts, pkg_ctx)

                succeeded_pkgs_ctx.append(pkg_ctx)
                os.symlink(pkg_ctx['workdir'], os.path.join( \
                    self.uh_recipes_succeed_dir, pkg_ctx['PN']))

                I(" %s: Upgrade SUCCESSFUL! Please test!" % pkg_ctx['PN'])
            except Exception as e:
                if isinstance(e, UpgradeNotNeededError):
                    I(" %s: %s" % (pkg_ctx['PN'], e.message))
                elif isinstance(e, UnsupportedProtocolError):
                    I(" %s: %s" % (pkg_ctx['PN'], e.message))
                else:
                    if not isinstance(e, Error):
                        import traceback
                        msg = "Failed(unknown error)\n" + traceback.format_exc()
                        e = Error(message=msg)
                        error = e

                    E(" %s: %s" % (pkg_ctx['PN'], e.message))

                    if os.listdir(pkg_ctx['workdir']):
                        E(" %s: Upgrade FAILED! Logs and/or file diffs are available in %s"
                            % (pkg_ctx['PN'], pkg_ctx['workdir']))

                pkg_ctx['error'] = e

                failed_pkgs_ctx.append(pkg_ctx)
                os.symlink(pkg_ctx['workdir'], os.path.join( \
                    self.uh_recipes_failed_dir, pkg_ctx['PN']))

            self.commit_changes(pkg_ctx)
            self.statistics.update(pkg_ctx['PN'], pkg_ctx['NPV'],
                    pkg_ctx['MAINTAINER'], pkg_ctx['error'])

        if self.opts['testimage']:
            if len(succeeded_pkgs_ctx) > 0:
                tim = TestImage(self.bb, self.git, self.uh_work_dir, succeeded_pkgs_ctx)

                try:
                    tim.prepare_branch()
                except Exception as e:
                    E(" testimage: Failed to prepare branch.")
                    if isinstance(e, Error):
                        E(" %s" % e.stdout)
                    exit(1)

                I(" Images will test for %s." % ', '.join(self.opts['machines']))
                for machine in self.opts['machines']:
                    I("  Testing images for %s ..." % machine)
                    try:
                        tim.ptest(machine)
                    except Exception as e:
                        E(" core-image-minimal/ptest on machine %s failed" % machine)
                        if isinstance(e, Error):
                            E(" %s" % e.stdout)
                        else:
                            import traceback
                            traceback.print_exc(file=sys.stdout)

                    image = settings.get('testimage_name', DEFAULT_TESTIMAGE)
                    try:
                        tim.testimage(machine, image)
                    except Exception as e:
                        E(" %s/testimage on machine %s failed" % (image, machine))
                        if isinstance(e, Error):
                            E(" %s" % e.stdout)
                        else:
                            import traceback
                            traceback.print_exc(file=sys.stdout)
            else:
                I(" Testimage was enabled but any upgrade was successful.")

        for pn in pkgs_ctx.keys():
            pkg_ctx = pkgs_ctx[pn]
            self.pkg_upgrade_handler(pkg_ctx)

        if attempted_pkgs > 0:
            statistics_summary = self.statistics.get_summary(
                    settings.get('publish_work_url', 'no'),
                    os.path.basename(self.uh_work_dir))

            statistics_file = os.path.join(self.uh_work_dir,
                    "statistics_summary")
            with open(statistics_file, "w+") as f:
                f.write(statistics_summary)

            I(" %s" % statistics_summary)

            if self.opts['send_email']:
                self.send_status_mail(statistics_summary)

class UniverseUpdater(Updater):
    def __init__(self):
        Updater.__init__(self, True, True)

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

    def _update_master(self):
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

    def _prepare(self):
        if settings.get("clean_sstate", "no") == "yes" and \
                os.path.exists(os.path.join(get_build_dir(), "sstate-cache")):
            I(" Removing sstate directory ...")
            shutil.rmtree(os.path.join(get_build_dir(), "sstate-cache"))
        if settings.get("clean_tmp", "no") == "yes" and \
                os.path.exists(os.path.join(get_build_dir(), "tmp")):
            I(" Removing tmp directory ...")
            shutil.rmtree(os.path.join(get_build_dir(), "tmp"))

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

    def _parse_checkpkg_file(self, file_path):
        import csv

        pkgs_list = []

        with open(file_path, "r") as f:
            reader = csv.reader(f, delimiter='\t')
            for row in reader:
                if reader.line_num == 1: # skip header line
                    continue

                pn = row[0]
                cur_ver = row[1]
                next_ver = row[2]
                status = row[11]
                maintainer = row[14]
                no_upgrade_reason = row[15]

                if status == 'UPDATE' and not no_upgrade_reason:
                    pkgs_list.append((pn, next_ver, maintainer))
                else:
                    if no_upgrade_reason:
                        D(" Skip package %s (status = %s, current version = %s," \
                            " next version = %s, no upgrade reason = %s)" %
                            (pn, status, cur_ver, next_ver, no_upgrade_reason))
                    else:
                        D(" Skip package %s (status = %s, current version = %s," \
                            " next version = %s)" %
                            (pn, status, cur_ver, next_ver))
        return pkgs_list

    # checks if maintainer is in whitelist and that the recipe itself is not
    # blacklisted: python, gcc, etc. Also, check the history if the recipe
    # hasn't already been tried
    def _pkg_upgradable(self, pn, next_ver, maintainer):
        if not maintainer:
            D(" Skipping upgrade of %s: no maintainer" % pn)
            return False

        if "blacklist" in settings:
            for p in settings["blacklist"].split():
                if p == pn:
                    D(" Skipping upgrade of %s: blacklist" % pn)
                    return False

        if "maintainers_whitelist" in settings:
            found = False
            for m in settings["maintainers_whitelist"].split():
                if maintainer.find(m) != -1:
                    found = True
                    break

            if found == False:
                D(" Skipping upgrade of %s: maintainer \"%s\" not in whitelist" %
                        (pn, maintainer))
                return False

        if pn in self.history:
            # did we already try this version?
            if next_ver == self.history[pn][0]:
                retry_delta = \
                    date.toordinal(date.today()) - \
                    date.toordinal(datetime.strptime(self.history[pn][2], '%Y-%m-%d'))
                # retry recipes that had fetch errors or other errors after
                # more than 30 days
                if (self.history[pn][3] == str(FetchError()) or
                        self.history[pn][3] == str(Error())) and retry_delta > 30:
                    return True

                D(" Skipping upgrade of %s: is in history and not 30 days passed" % pn)
                return False

        # drop native/cross/cross-canadian recipes. We deal with native
        # when upgrading the main recipe but we keep away of cross* pkgs...
        # for now
        if pn.find("cross") != -1 or pn.find("native") != -1:
            D(" Skipping upgrade of %s: is cross or native" % pn)
            return False

        return True

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
        for pkg in self._parse_checkpkg_file(last_checkpkg_file):
            if self._pkg_upgradable(pkg[0], pkg[1], pkg[2]):
                pkgs_list.append(pkg)

        # Update last_checkpkg_run only after the version check has been completed
        with open(get_build_dir() + "/upgrade-helper/last_checkpkg_run", "w+") as last_check:
            last_check.write(current_date + "," + cur_master_commit + "," +
                             last_checkpkg_file)

        return pkgs_list

    def _update_history(self, pn, new_ver, maintainer, upgrade_status):
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

    def pkg_upgrade_handler(self, pkg_ctx):
        super(UniverseUpdater, self).pkg_upgrade_handler(pkg_ctx)
        self._update_history(pkg_ctx['PN'], pkg_ctx['NPV'], pkg_ctx['MAINTAINER'],
                self._get_status_msg(pkg_ctx['error']))

    def run(self):
        self._update_master()
        self._prepare()
        super(UniverseUpdater, self).run()

def close_child_processes(signal_id, frame):
    pid = os.getpgrp()
    os.killpg(pid, signal.SIGKILL)

if __name__ == "__main__":
    global settings
    global maintainer_override

    if not os.getenv('BUILDDIR', False):
        E(" You must source oe-init-build-env before running this script!\n")
        exit(1)

    devnull = open(os.devnull, 'wb')
    if subprocess.call(["git", "config", "user.name"], stdout=devnull,stderr=devnull) or \
        subprocess.call(["git", "config", "user.email"], stdout=devnull, stderr=devnull):
        E(" Git isn't configured please configure user name and email\n")
        exit(1)

    signal.signal(signal.SIGINT, close_child_processes)

    debug_levels = [log.CRITICAL, log.ERROR, log.WARNING, log.INFO, log.DEBUG]
    args = parse_cmdline()
    log.basicConfig(format='%(levelname)s:%(message)s',
                    level=debug_levels[args.debug_level - 1])
    settings, maintainer_override = parse_config_file(args.config_file)

    if args.recipe == "all":
        updater = UniverseUpdater()
        updater.run()
    else:
        if not args.to_version:
            E(" For upgrade only one recipe you must specify --to_version\n")
            exit(1)

        if not args.maintainer and args.send_emails:
            E(" For upgrade only one recipe and send email you must specify --maintainer\n")
            exit(1)

        pkg_list = [(args.recipe, args.to_version, args.maintainer)]
        updater = Updater(args.auto_mode, args.send_emails, args.skip_compilation)
        updater.run(pkg_list)
