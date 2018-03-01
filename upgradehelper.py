#!/usr/bin/env python3
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
import configparser as cp
from datetime import datetime
from datetime import date
import shutil

sys.path.insert(1, os.path.join(os.path.abspath(
    os.path.dirname(__file__)), 'modules'))

from errors import *

from utils.git import Git
from utils.devtool import Devtool
from utils.bitbake import *
from utils.emailhandler import Email

from statistics import Statistics
from steps import upgrade_steps
from testimage import TestImage

help_text = """Usage examples:
* To upgrade xmodmap recipe to the latest available version:
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
    parser.add_argument("recipe", nargs = '+', action='store', default='', help="recipe to be upgraded")

    parser.add_argument("-t", "--to_version",
                        help="version to upgrade the recipe to")

    parser.add_argument("-d", "--debug-level", type=int, default=4, choices=range(1, 6),
                        help="set the debug level: CRITICAL=1, ERROR=2, WARNING=3, INFO=4, DEBUG=5")
    parser.add_argument("-e", "--send-emails", action="store_true", default=False,
                        help="send emails to recipe maintainers")
    parser.add_argument("-s", "--skip-compilation", action="store_true", default=False,
                        help="do not compile, just change the checksums, remove PR, and commit")
    parser.add_argument("-c", "--config-file", default=None,
                        help="Path to the configuration file. Default is $BUILDDIR/upgrade-helper/upgrade-helper.conf")
    return parser.parse_args()

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
    def __init__(self, args):
        build_dir = get_build_dir()

        self.bb = Bitbake(build_dir)
        self.devtool = Devtool()
        self.args = args

        try:
            self.base_env = self.bb.env()
        except EmptyEnvError as e:
            import traceback
            E( " %s\n%s" % (e.message, traceback.format_exc()))
            E( " Bitbake output:\n%s" % (e.stdout))
            exit(1)

        self._set_options()

        self._make_dirs(build_dir)

        self._add_file_logger()

        if self.args.send_emails:
            self.email_handler = Email(settings)
        self.statistics = Statistics()

    def _set_options(self):
        self.opts = {}
        self.opts['layer_mode'] = settings.get('layer_mode', '')
        if self.opts['layer_mode'] == 'yes':
            def _layer_settings_error(setting):
                E(" In layer mode enable you need to specify %s.\n" % setting)
                exit(1)

            layer_settings = ('layer_name', 'layer_dir', 'layer_machines')
            for s in layer_settings:
                self.opts[s] = settings.get(s, '')
                if not self.opts[s]:
                    _layer_settings_error(s)

            self.git = Git(self.opts['layer_dir'])
            self.poky_git = Git(os.path.dirname(os.getenv('PATH', False).split(':')[0]))
            self.opts['machines'] = self.opts['layer_machines'].split()
        else:
            # XXX: assume that the poky directory is the first entry in the PATH
            self.git = Git(os.path.dirname(os.getenv('PATH', False).split(':')[0]))
            self.poky_git = None
            self.opts['machines'] = settings.get('machines',
                'qemux86 qemux86-64 qemuarm qemumips qemuppc qemux86_musl').split()

        self.opts['send_email'] = self.args.send_emails
        self.opts['author'] = "Upgrade Helper <%s>" % \
                settings.get('from', 'uh@not.set')
        self.opts['skip_compilation'] = self.args.skip_compilation
        self.opts['buildhistory'] = self._buildhistory_is_enabled()
        self.opts['testimage'] = self._testimage_is_enabled()

    def _make_dirs(self, build_dir):
        self.uh_dir = os.path.join(build_dir, "upgrade-helper")
        if not os.path.exists(self.uh_dir):
            os.mkdir(self.uh_dir)
        self.uh_base_work_dir = settings.get('workdir', '')
        if not self.uh_base_work_dir:
            self.uh_base_work_dir = self.uh_dir
        if self.opts['layer_mode'] == 'yes':
            self.uh_base_work_dir = os.path.join(self.uh_base_work_dir,
                    self.opts['layer_name'])
        if not os.path.exists(self.uh_base_work_dir):
            os.mkdir(self.uh_base_work_dir)
        self.uh_work_dir = os.path.join(self.uh_base_work_dir, "%s" % \
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

        return enabled

    def _testimage_is_enabled(self):
        enabled = False

        if settings.get("testimage", "no") == "yes":
            if 'testimage' in self.base_env['INHERIT']:
                if not "ptest" in self.base_env["DISTRO_FEATURES"]:
                    E(" testimage requires ptest in DISTRO_FEATURES please add to"\
                      " conf/local.conf.")
                    exit(1)

                enabled = True
            else:
                E(" testimage was enabled in upgrade-helper.conf"\
                  " but isn't INHERIT in conf/local.conf, if you want"\
                  " to enable please set.")
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
        mail_header = \
            "Hello,\n\nYou are receiving this email because you are the maintainer\n" \
            "of *%s* recipe and this is to let you know that the automatic attempt\n" \
            "to upgrade the recipe to *%s* has %s.\n\n"

        license_change_info = \
            "*LICENSE CHANGED* please review the %s file, update the LICENSE\n" \
            "variable in the recipe if needed and summarize the changes\n" \
            "in the commit message via 'License-Update:' tag.\n" \
            "(example: 'License-Update: copyright years updated.')\n\n"

        next_steps_info = \
            "Next steps:\n" \
            "    - apply the patch: git am %s\n" \
            "    - check the changes to upstream patches and summarize them in the commit message,\n" \
            "    - compile an image that contains the package\n" \
            "    - perform some basic sanity tests\n" \
            "    - amend the patch and sign it off: git commit -s --reset-author --amend\n" \
            "    - send it to the appropriate mailing list\n\n" \
            "Alternatively, if you believe the recipe should not be upgraded at this time,\n" \
            "you can fill RECIPE_NO_UPDATE_REASON in respective recipe file so that\n" \
            "automatic upgrades would no longer be attempted.\n\n"

        mail_footer = \
            "Please review the attached files for further information and build/update failures.\n" \
            "Any problem please file a bug at https://bugzilla.yoctoproject.org/enter_bug.cgi?product=Automated%20Update%20Handler\n\n" \
            "Regards,\nThe Upgrade Helper"

        if pkg_ctx['MAINTAINER'] in maintainer_override:
            to_addr = maintainer_override[pkg_ctx['MAINTAINER']]
        elif 'global_maintainer_override' in settings:
            to_addr = settings['global_maintainer_override']
        else:
            to_addr = pkg_ctx['MAINTAINER']

        cc_addr = None
        if "status_recipients" in settings:
            cc_addr = settings["status_recipients"].split()

        newversion = pkg_ctx['NPV'] if not pkg_ctx['NPV'].endswith("new-commits-available") else pkg_ctx['NSRCREV']
        subject = "[AUH] " + pkg_ctx['PN'] + ": upgrading to " + newversion
        if not pkg_ctx['error']:
            subject += " SUCCEEDED"
        else:
            subject += " FAILED"
        msg_body = mail_header % (pkg_ctx['PN'], newversion,
                self._get_status_msg(pkg_ctx['error']))

        if pkg_ctx['error'] is not None:
            msg_body += """Detailed error information:

%s
%s
%s

""" %(pkg_ctx['error'].message if pkg_ctx['error'].message else "", pkg_ctx['error'].stdout if pkg_ctx['error'].stdout else "" , pkg_ctx['error'].stderr if pkg_ctx['error'].stderr else "")

        if 'license_diff_fn' in pkg_ctx:
            license_diff_fn = pkg_ctx['license_diff_fn']
            msg_body += license_change_info % license_diff_fn

        if 'patch_file' in pkg_ctx and pkg_ctx['patch_file'] != None:
            msg_body += next_steps_info % (os.path.basename(pkg_ctx['patch_file']))

        msg_body += mail_footer

        # Add possible attachments to email
        attachments = []
        for attachment in os.listdir(pkg_ctx['workdir']):
            attachment_fullpath = os.path.join(pkg_ctx['workdir'], attachment)
            if os.path.isfile(attachment_fullpath):
                attachments.append(attachment_fullpath)

        if self.opts['send_email']:
            self.email_handler.send_email(to_addr, subject, msg_body, attachments, cc_addr=cc_addr)
        # Preserve email for review purposes.
        email_file = os.path.join(pkg_ctx['workdir'],
                    "email_summary")
        with open(email_file, "w+") as f:
            f.write("To: %s\n" % to_addr)
            if isinstance(cc_addr, list):
                f.write("To: %s\n" % ' '.join(cc_addr))
            else:
                f.write("Cc: %s\n" % cc_addr)

            f.write("Subject: %s\n" % subject)
            f.write("Attachments: %s\n" % ' '.join(attachments))
            f.write("\n%s\n" % msg_body)

    def commit_changes(self, pkg_ctx):
        try:
            pkg_ctx['patch_file'] = None

            I(" %s: Auto commit changes ..." % pkg_ctx['PN'])
            self.git.add(pkg_ctx['recipe_dir'])
            self.git.commit(pkg_ctx['commit_msg'], self.opts['author'])

            stdout = self.git.create_patch(pkg_ctx['workdir'])
            pkg_ctx['patch_file'] = stdout.strip()

            if not pkg_ctx['patch_file']:
                msg = "Patch file not generated."
                E(" %s: %s\n %s" % (pkg_ctx['PN'], msg, stdout))
                raise Error(msg, stdout)
            else:
                I(" %s: Save patch in directory: %s." %
                    (pkg_ctx['PN'], pkg_ctx['workdir']))
            if pkg_ctx['error'] is not None:
                I("Due to build errors, the commit will also be reverted to avoid cascading upgrade failures.")
                self.git.revert("HEAD")
        except Error as e:
            msg = ''

            for line in e.stdout.split("\n"):
                if line.find("nothing to commit") == 0:
                    msg = "Nothing to commit!"
                    I(" %s: %s" % (pkg_ctx['PN'], msg))

            I(" %s: %s" % (pkg_ctx['PN'], e.stdout))
            raise e

    def send_status_mail(self, statistics_summary):
        if "status_recipients" not in settings:
            E(" Could not send status email, no recipients set!")
            return -1

        to_list = settings["status_recipients"].split()

        if self.opts['layer_mode'] == 'yes':
            subject = "[AUH] Upgrade status %s: %s" \
                    % (self.opts['layer_name'], date.isoformat(date.today()))
        else:
            subject = "[AUH] Upgrade status: " + date.isoformat(date.today())

        if self.statistics.total_attempted:
            self.email_handler.send_email(to_list, subject, statistics_summary)
        else:
            W("No recipes attempted, not sending status mail!")

    def run(self, package_list=None):
        pkgs_to_upgrade = self._get_packages_to_upgrade(package_list)
        total_pkgs = len(pkgs_to_upgrade)

        pkgs_ctx = {}

        I(" ########### The list of recipes to be upgraded #############")
        for p, ov, nv, m, r in pkgs_to_upgrade:
            I(" %s, %s, %s, %s, %s" % (p, ov, nv, m, r))

            pkgs_ctx[p] = {}
            pkgs_ctx[p]['PN'] = p
            pkgs_ctx[p]['PV'] = ov
            pkgs_ctx[p]['NPV'] = nv
            pkgs_ctx[p]['MAINTAINER'] = m
            pkgs_ctx[p]['NSRCREV'] = r

            pkgs_ctx[p]['base_dir'] = self.uh_recipes_all_dir
        I(" ############################################################")

        if pkgs_to_upgrade and not self.args.skip_compilation:
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

        succeeded_pkgs_ctx = []
        failed_pkgs_ctx = []
        attempted_pkgs = 0
        for pn, _, _, _, _ in pkgs_to_upgrade:
            pkg_ctx = pkgs_ctx[pn]
            pkg_ctx['error'] = None

            attempted_pkgs += 1
            I(" ATTEMPT PACKAGE %d/%d" % (attempted_pkgs, total_pkgs))
            try:
                I(" %s: Upgrading to %s" % (pkg_ctx['PN'], pkg_ctx['NPV']))
                for step, msg in upgrade_steps:
                    if msg is not None:
                        I(" %s: %s" % (pkg_ctx['PN'], msg))
                    step(self.devtool, self.bb, self.git, self.opts, pkg_ctx)
                succeeded_pkgs_ctx.append(pkg_ctx)

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

                    if 'workdir' in pkg_ctx and os.listdir(pkg_ctx['workdir']):
                        E(" %s: Upgrade FAILED! Logs and/or file diffs are available in %s"
                            % (pkg_ctx['PN'], pkg_ctx['workdir']))

                pkg_ctx['error'] = e
                failed_pkgs_ctx.append(pkg_ctx)

            try:
                self.commit_changes(pkg_ctx)
            except:
                if pkg_ctx in succeeded_pkgs_ctx:
                    succeeded_pkgs_ctx.remove(pkg_ctx)
                    failed_pkgs_ctx.append(pkg_ctx)

        if self.opts['testimage']:
            ctxs = {}
            ctxs['succeeded'] = succeeded_pkgs_ctx
            ctxs['failed'] = failed_pkgs_ctx
            image = settings.get('testimage_name', DEFAULT_TESTIMAGE)
            tim = TestImage(self.bb, self.git, self.uh_work_dir, self.opts,
                   ctxs, image)

            tim.run()

        for pn in pkgs_ctx.keys():
            pkg_ctx = pkgs_ctx[pn]

            if pkg_ctx in succeeded_pkgs_ctx:
                os.symlink(pkg_ctx['workdir'], os.path.join( \
                    self.uh_recipes_succeed_dir, pkg_ctx['PN']))
            else:
                os.symlink(pkg_ctx['workdir'], os.path.join( \
                    self.uh_recipes_failed_dir, pkg_ctx['PN']))

            self.statistics.update(pkg_ctx['PN'], pkg_ctx['NPV'],
                    pkg_ctx['MAINTAINER'], pkg_ctx['error'])
            self.pkg_upgrade_handler(pkg_ctx)

        if attempted_pkgs > 0:
            publish_work_url = settings.get('publish_work_url', '')
            work_tarball = os.path.join(self.uh_base_work_dir,
                    os.path.basename(self.uh_work_dir) + '.tar.gz')
            if publish_work_url:
                I(" Generating work tarball in %s ..." % work_tarball)
                tar_cmd = ["tar", "-chzf", work_tarball, "-C", self.uh_base_work_dir, os.path.basename(self.uh_work_dir)]
                import subprocess
                if subprocess.call(tar_cmd):
                    E(" Work tarball (%s) generation failed..." % (work_tarball))
                    E(" Tar command: %s" % (" ".join(tar_cmd)))
                    publish_work_url = ''

            statistics_summary = self.statistics.get_summary(
                    publish_work_url, os.path.basename(self.uh_work_dir))

            statistics_file = os.path.join(self.uh_work_dir,
                    "statistics_summary")
            with open(statistics_file, "w+") as f:
                f.write(statistics_summary)

            I(" %s" % statistics_summary)

            if self.opts['send_email']:
                self.send_status_mail(statistics_summary)

class UniverseUpdater(Updater):
    def __init__(self, args):
        Updater.__init__(self, args)

        if len(args.recipe) == 1 and args.recipe[0] == "all":
            self.recipes = []
        else:
            self.recipes = args.recipe

        # to filter recipes in upgrade
        if not self.recipes and self.opts['layer_mode'] == 'yes':
            # when layer mode is enabled and no recipes are specified
            # we need to figure out what recipes are provided by the
            # layer to try upgrade
            self.recipes = self._get_recipes_by_layer()

        if args.to_version:
            if len(self.recipes) != 1:
                E(" -t is only supported when upgrade one recipe\n")
                exit(1)

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
    def _get_recipes_by_layer(self):
        recipes = []

        recipe_regex = re.compile('^(?P<name>.*):$')
        layer_regex = re.compile('^  (?P<name>.*) +')

        layers = False
        name = ''

        output = subprocess.check_output('bitbake-layers show-recipes',
                shell=True)
        for line in output.decode("utf-8") .split('\n'):
            s = recipe_regex.search(line)
            if s:
                name = s.group('name')
                continue

            if not 'skipped' in line:
                s = layer_regex.search(line)
                if s:
                    if s.group('name').strip() == self.opts['layer_name']:
                        recipes.append(name)

        return recipes

    def _prepare(self):
        if settings.get("clean_sstate", "no") == "yes" and \
                os.path.exists(os.path.join(get_build_dir(), "sstate-cache")):
            I(" Removing sstate directory ...")
            shutil.rmtree(os.path.join(get_build_dir(), "sstate-cache"))
        if settings.get("clean_tmp", "no") == "yes" and \
                os.path.exists(self.base_env['TMPDIR']):
            I(" Removing tmp directory ...")
            shutil.rmtree(self.base_env['TMPDIR'])

    def _check_upstream_versions(self):
        I(" Fetching upstream version(s) ...")

        if self.recipes:
            recipe = " ".join(self.recipes)
        else:
            recipe = 'universe'

        try:
            self.bb.checkpkg(recipe)
        except Error as e:
            for line in e.stdout.split('\n'):
                if line.find("ERROR: Task do_checkpkg does not exist") != -1:
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
                if self.args.to_version:
                    next_ver = self.args.to_version
                else:
                    next_ver = row[2]
                status = row[11]
                revision = row[12]
                maintainer = row[14]
                no_upgrade_reason = row[15]

                if status == 'UPDATE' and not no_upgrade_reason:
                    pkgs_list.append((pn, cur_ver, next_ver, maintainer, revision))
                else:
                    if no_upgrade_reason:
                        I(" Skip package %s (status = %s, current version = %s," \
                            " next version = %s, no upgrade reason = %s)" %
                            (pn, status, cur_ver, next_ver, no_upgrade_reason))
                    else:
                        I(" Skip package %s (status = %s, current version = %s," \
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
        self._check_upstream_versions()
        last_checkpkg_file = os.path.realpath(self.base_env['TMPDIR'] + "/log/checkpkg.csv")

        pkgs_list = []
        for pkg in self._parse_checkpkg_file(last_checkpkg_file):
            # Always do the upgrade if recipes are specified
            if self.recipes and pkg[0] in self.recipes:
                pkgs_list.append(pkg)
            elif self._pkg_upgradable(pkg[0], pkg[2], pkg[3]):
                pkgs_list.append(pkg)

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
        E(" It is recommended to create a fresh build directory with it:\n")
        E(" $ . oe-init-build-env build-auh\n")
        exit(1)

    devnull = open(os.devnull, 'wb')
    if subprocess.call(["git", "config", "user.name"], stdout=devnull,stderr=devnull) or \
        subprocess.call(["git", "config", "user.email"], stdout=devnull, stderr=devnull):
        E(" Git isn't configured please configure user name and email\n")
        exit(1)

    with open(os.getenv('BUILDDIR')+"/conf/local.conf") as f:
        import re
        for line in f.readlines():
            if re.match(r"^MACHINE\s*=", line):
                E(" The following line found in local.conf - please use ?= or ?== instead as otherwise AUH will not be able to set the desired target machine\n")
                E(" {}".format(line))
                exit(1)
            if re.match(r"^TCLIBC\s*=", line):
                E(" The following line found in local.conf - please use ?= or ?== instead as otherwise AUH will not be able to set the desired C library\n")
                E(" {}".format(line))
                exit(1)

    signal.signal(signal.SIGINT, close_child_processes)

    debug_levels = [log.CRITICAL, log.ERROR, log.WARNING, log.INFO, log.DEBUG]
    args = parse_cmdline()
    log.basicConfig(format='%(levelname)s:%(message)s',
                    level=debug_levels[args.debug_level - 1])
    settings, maintainer_override = parse_config_file(args.config_file)

    updater = UniverseUpdater(args)
    updater.run()
