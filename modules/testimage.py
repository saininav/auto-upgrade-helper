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
    def __init__(self, bb, git, uh_work_dir, opts, packages, image):
        self.bb = bb
        self.git = git
        self.uh_work_dir = uh_work_dir
        self.opts = opts
        self.pkgs_ctx = packages['succeeded']
        self.image = image

        os.environ['BB_ENV_EXTRAWHITE'] = os.environ['BB_ENV_EXTRAWHITE'] + \
            " TEST_SUITES CORE_IMAGE_EXTRA_INSTALL"

    def _get_pkgs_to_install(self, pkgs, ptest=False):
        pkgs_out = []

        # for provide access to the target
        if ptest:
            pkgs_out.append("dropbear")
            pkgs_out.append("ptest-runner")

        for c in pkgs:
            pkgs_out.append(c['PN'])

        return ' '.join(pkgs_out)

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

        D("Found logs named %s for machine %s: %s" %(name, machine, result))
        for ptest_log in result:
            if machine in ptest_log:
                D("Picked log: %s" %(ptest_log))
                return ptest_log

    def _get_failed_recipe(self, log):
        pn = None

        for line in log.splitlines():
            m = re.match("ERROR: QA Issue: ([^ :]*): (.*) not shipped", line)
            if m:
                pn = m.group(1)
                break

            m = re.match("ERROR: Logfile of failure stored in: " \
                "(.*/([^/]*)/[^/]*/temp/log\.(.*)\.[0-9]*)", line)
            if m:
                pn = m.group(2)
                break

        return pn

    def _handle_image_build_error(self, image, pkgs_ctx, e):
        pn = self._get_failed_recipe(e.stdout)
        if pn and pn != image:
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

    def testimage(self, pkgs_ctx, machine, image):
        os.environ['CORE_IMAGE_EXTRA_INSTALL'] = \
            self._get_pkgs_to_install(pkgs_ctx)

        if 'TEST_SUITES' in os.environ:
            del os.environ['TEST_SUITES']

        I( "   building %s for %s ..." % (image, machine))
        try:
            self.bb.complete(image, machine)
        except Error as e:
            self._handle_image_build_error(image, pkgs_ctx, e)

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

    def run(self):
        machine = self.opts['machines'][0]
        I("  Testing image for %s ..." % machine)
        self.testimage(self.pkgs_ctx, machine, self.image)
