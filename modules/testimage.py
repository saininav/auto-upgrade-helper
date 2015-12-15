#!/usr/bin/env python
# vim: set ts=4 sw=4 et:
#
# Copyright (c) 2015 Intel Corporation
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
# This module implements logic for run image tests on recipes when upgrade
# process succeed.
#

import os
import sys
import shutil

import logging as log
from logging import debug as D
from logging import info as I
from logging import warning as W
from logging import error as E
from logging import critical as C

from errors import *
from utils.bitbake import *

def _pn_in_pkgs_ctx(pn, pkgs_ctx):
    for c in pkgs_ctx:
        if pn == c['PN']:
            return c
    return None

class TestImage():
    def __init__(self, bb, git, uh_work_dir, opts, *args, **kwargs):
        self.bb = bb
        self.git = git
        self.uh_work_dir = uh_work_dir
        self.opts = opts
        self.pkgs_ctx = args[0]
        self.image = args[1]

        os.environ['BB_ENV_EXTRAWHITE'] = os.environ['BB_ENV_EXTRAWHITE'] + \
            " TEST_SUITES CORE_IMAGE_EXTRA_INSTALL"

    def _get_ptest_pkgs(self, pkgs_ctx):
        pkgs = []

        for c in pkgs_ctx:
            if "ptest" in c['recipe'].get_inherits():
                pkgs.append(c)

        return pkgs

    def _get_pkgs_to_install(self, pkgs, ptest=False):
        pkgs_out = []

        # for provide access to the target
        if ptest:
            pkgs_out.append("dropbear")
            pkgs_out.append("ptest-runner")

        for c in pkgs:
            pkgs_out.append(c['PN'])

        return ' '.join(pkgs_out)

    def prepare_branch(self, pkgs_ctx):
        ok = False

        try:
            self.git.reset_hard()
            self.git.checkout_branch("master")

            try:
                self.git.delete_branch("testimage")
            except Error:
                pass

            self.git.create_branch("testimage")
            for c in pkgs_ctx:
                patch_file = os.path.join(c['workdir'], c['patch_file'])
                self.git.apply_patch(patch_file)

            ok = True
        except Exception as e:
            E(error_msg)
            self._log_error(" testimage: Failed to prepare branch.")

        return ok
 
    def _parse_ptest_log(self, log_file):
        ptest_results = {}

        with open(log_file, "r") as f:
            pn = None
            processing = False

            for line in f:
                if not processing:
                    m = re.search("^BEGIN: /usr/lib/(.*)/ptest$", line)
                    if m:
                        pn = m.group(1)
                        ptest_results[pn] = []
                        processing = True
                else:
                    m = re.search("^END: $", line)
                    if m:
                        pn = None
                        processing = False
                    else:
                        ptest_results[pn].append(line)

        return ptest_results

    def _find_log(self, name, machine):
        result = []

        base_dir = os.path.join(os.getenv('BUILDDIR'), 'tmp', 'work')
        for root, dirs, files in os.walk(base_dir):
            if name in files:
                result.append(os.path.join(root, name))

        for ptest_log in result:
            if machine in ptest_log:
                return ptest_log

    def _get_failed_recipe(self, log):
        pn = None

        for line in log.splitlines():
            m = re.match("ERROR: Logfile of failure stored in: " \
                "(.*/([^/]*)/[^/]*/temp/log\.(.*)\.[0-9]*)", line)
            if m:
                pn = m.group(2)
                break

        return pn

    def _handle_image_build_error(self, pkgs_ctx, e):
        pn = self._get_failed_recipe(e.stdout)
        if pn:
            pkg_ctx = _pn_in_pkgs_ctx(pn, pkgs_ctx)
            if pkg_ctx:
                raise IntegrationError(e.stdout, pkg_ctx)
            else:
                pn_env = self.bb.env(pn)

                depends = pn_env['DEPENDS'].split()
                rdepends = pn_env['RDEPENDS'].split()
                deps = depends + rdepends

                for d in deps:
                    pkg_ctx = _pn_in_pkgs_ctx(d, pkgs_ctx)
                    if pkg_ctx:
                        raise IntegrationError(e.stdout, pkg_ctx)
        raise e

    def ptest(self, pkgs_ctx, machine):
        ptest_pkgs = self._get_ptest_pkgs(pkgs_ctx)

        os.environ['CORE_IMAGE_EXTRA_INSTALL'] = \
            self._get_pkgs_to_install(ptest_pkgs, ptest=True)
        I( "   building core-image-minimal for %s ..." % machine)
        try:
            self.bb.complete("core-image-minimal", machine)
        except Error as e:
            self._handle_image_build_error(pkgs_ctx, e)

        os.environ['TEST_SUITES'] = "ping ssh _ptest"
        I( "   running core-image-minimal/ptest for %s ..." % machine)
        self.bb.complete("core-image-minimal -c testimage", machine)

        ptest_log_file = self._find_log("ptest.log", machine)
        shutil.copyfile(ptest_log_file,
                os.path.join(self.uh_work_dir, "ptest_%s.log" % machine))

        ptest_result = self._parse_ptest_log(ptest_log_file)
        for pn in ptest_result:
            for pkg_ctx in pkgs_ctx:
                if not pn == pkg_ctx['PN']:
                    continue 

                if not 'ptest' in pkg_ctx:
                    pkg_ctx['ptest'] = {}
                if not 'ptest_log' in pkg_ctx:
                    pkg_ctx['ptest_log'] = os.path.join(pkg_ctx['workdir'],
                        "ptest.log")

                pkg_ctx['ptest'][machine] = True
                with open(pkg_ctx['ptest_log'], "a+") as f:
                    f.write("BEGIN: PTEST for %s\n" % machine)
                    for line in ptest_result[pn]:
                        f.write(line)
                    f.write("END: PTEST for %s\n" % machine)

    def testimage(self, pkgs_ctx, machine, image):
        os.environ['CORE_IMAGE_EXTRA_INSTALL'] = \
            self._get_pkgs_to_install(pkgs_ctx)

        if 'TEST_SUITES' in os.environ:
            del os.environ['TEST_SUITES']

        I( "   building %s for %s ..." % (image, machine))
        try:
            self.bb.complete(image, machine)
        except Error as e:
            self._handle_image_build_error(pkgs_ctx, e)

        I( "   running %s/testimage for %s ..." % (image, machine))
        self.bb.complete("%s -c testimage" % image, machine)

        log_file = self._find_log("log.do_testimage", machine)
        shutil.copyfile(log_file,
                os.path.join(self.uh_work_dir, "log_%s.do_testimage" % machine))
        for pkg_ctx in pkgs_ctx:
            if not 'testimage' in pkg_ctx:
                pkg_ctx['testimage'] = {}
            if not 'testimage_log' in pkg_ctx:
                pkg_ctx['testimage_log'] = os.path.join(
                    pkg_ctx['workdir'], "log.do_testimage")

            pkg_ctx['testimage'][machine] = True
            with open(log_file, "r") as lf:
                with open(pkg_ctx['testimage_log'], "a+") as of:
                    of.write("BEGIN: TESTIMAGE for %s\n" % machine)
                    for line in lf:
                        of.write(line)
                    of.write("END: TESTIMAGE for %s\n" % machine)

    def _log_error(self, e):
        if isinstance(e, Error):
            E(" %s" % e.stdout)
        else:
            import traceback
            tb = traceback.format_exc()
            E("%s" % tb)

    def _handle_error(self, e, machine):
        handled = True

        if isinstance(e, IntegrationError):
            pkg_ctx = e.pkg_ctx

            E("   %s on machine %s failed in integration, removing..."
                % (pkg_ctx['PN'], machine))

            with open(os.path.join(pkg_ctx['workdir'],
                'integration_error.log'), 'a+') as f:
                f.write(e.stdout)

            if not pkg_ctx in self.pkgs_ctx['succeeded']:
                E( "Infinite loop IntegrationError trying to " \
                   "remove %s twice, see logs.", pkg_ctx['PN'])
                handled = False
            else:
                pkg_ctx['error'] = e

                # remove previous build tmp, sstate to avoid QA errors
                # on lower versions
                I("     removing sstate directory ...")
                shutil.rmtree(os.path.join(get_build_dir(), "sstate-cache"))
                I("     removing tmp directory ...")
                shutil.rmtree(os.path.join(get_build_dir(), "tmp"))

                self.pkgs_ctx['failed'].append(pkg_ctx)
                self.pkgs_ctx['succeeded'].remove(pkg_ctx)

                if not self.prepare_branch(self.pkgs_ctx['succeeded']):
                    handled = False
        else:
            handled = False

        return handled

    def run(self):
        if len(self.pkgs_ctx['succeeded']) <= 0:
            I(" Testimage was enabled but any upgrade was successful.")
            return

        if not self.prepare_branch(self.pkgs_ctx['succeeded']):
           return

        I(" Images will test for %s." % ', '.join(self.opts['machines']))
        for machine in self.opts['machines']:
            I("  Testing images for %s ..." % machine)
            while True:
                try:
                    self.ptest(self.pkgs_ctx['succeeded'], machine)
                    break
                except Exception as e:
                    if not self._handle_error(e, machine):
                        E(" %s/testimage on machine %s failed" % (self.image, machine))
                        self._log_error(e)
                        break

            while True:
                try:
                    self.testimage(self.pkgs_ctx['succeeded'], machine, self.image)
                    break
                except Exception as e:
                    if not self._handle_error(e, machine):
                        E(" %s/testimage on machine %s failed" % (self.image, machine))
                        self._log_error(e)
                        break
