# SPDX-License-Identifier: GPL-2.0-or-later
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

        self.logdir = os.path.join(uh_work_dir, "testimage-logs")
        os.mkdir(self.logdir)

        os.environ['BB_ENV_EXTRAWHITE'] = os.environ['BB_ENV_EXTRAWHITE'] + \
            " CORE_IMAGE_EXTRA_INSTALL TEST_LOG_DIR TESTIMAGE_UPDATE_VARS"

    def _get_pkgs_to_install(self, pkgs):
        pkgs_out = []

        for c in pkgs:
            pkgs_out.append(c['PN'])

            I(" Checking if package {} has ptests...".format(c['PN']))
            if 'PTEST_ENABLED' in self.bb.env(c['PN']):
                I("  ...yes")
                pkgs_out.append((c['PN']) + '-ptest')
            else:
                I("  ...no")

        return ' '.join(pkgs_out)

    def testimage(self, pkgs_ctx, machine, image):
        os.environ['CORE_IMAGE_EXTRA_INSTALL'] = \
            self._get_pkgs_to_install(pkgs_ctx)
        os.environ['TEST_LOG_DIR'] = self.logdir
        os.environ['TESTIMAGE_UPDATE_VARS'] = 'TEST_LOG_DIR'
        I( " Installing additional packages to the image: {}".format(os.environ['CORE_IMAGE_EXTRA_INSTALL']))

        I( "   building %s for %s ..." % (image, machine))
        bitbake_create_output = ""
        bitbake_run_output = ""
        try:
            bitbake_create_output = self.bb.complete(image, machine)
        except Error as e:
            I( "   building the testimage failed! Collecting logs...")
            bitbake_create_output = e.stdout
        else:
            I( "   running %s/testimage for %s ..." % (image, machine))
            try:
                bitbake_run_output = self.bb.complete("%s -c testimage" % image, machine)
            except Error as e:
                I( "   running the testimage failed! Collecting logs...")
                bitbake_run_output = e.stdout

        if bitbake_create_output:
            with open(os.path.join(self.logdir, "bitbake-create-testimage.log"), 'w') as f:
                f.write(bitbake_create_output)
        if bitbake_run_output:
            with open(os.path.join(self.logdir, "bitbake-run-testimage.log"), 'w') as f:
                f.write(bitbake_run_output)
        I(" All done! Testimage/ptest/qemu logs are collected to {}".format(self.logdir))

    def run(self):
        machine = self.opts['machines'][0]
        I("  Testing image for %s ..." % machine)
        self.testimage(self.pkgs_ctx, machine, self.image)
