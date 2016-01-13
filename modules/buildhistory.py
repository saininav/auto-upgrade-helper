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

import os
import logging as log
from logging import debug as D
from logging import info as I
from logging import warning as W
from logging import error as E
from logging import critical as C
import sys

from errors import *
from utils.git import Git
from utils.bitbake import *

class BuildHistory(object):
    def __init__(self, bb, pn, workdir):
        self.bb = bb
        self.pn = pn
        self.workdir = workdir
        self.revs = []

        self.buildhistory_dir = os.path.join(self.workdir, 'buildhistory')
        if not os.path.exists(self.buildhistory_dir):
            os.mkdir(self.buildhistory_dir)

        self.git = Git(self.buildhistory_dir)

        os.environ['BB_ENV_EXTRAWHITE'] = os.environ['BB_ENV_EXTRAWHITE'] + \
                                    " BUILDHISTORY_DIR"
        os.environ["BUILDHISTORY_DIR"] = self.buildhistory_dir

    def init(self, machines):
        self.bb.cleanall(self.pn)
        for machine in machines:
            self.bb.complete(self.pn, machine)
            self.revs.append(self.git.last_commit("master"))

    def add(self):
        self.revs.append(self.git.last_commit("master"))

    def diff(self):
        rev_initial = self.revs[0]
        rev_final = self.revs[-1]

        try:
            cmd = "buildhistory-diff -p %s %s %s"  % (self.buildhistory_dir, 
                rev_initial, rev_final)
            stdout, stderr = bb.process.run(cmd)
            if stdout and os.path.exists(self.workdir):
                with open(os.path.join(self.workdir, "buildhistory-diff.txt"),
                        "w+") as log:
                    log.write(stdout)

            cmd_full = "buildhistory-diff -a -p %s %s %s"  % (self.buildhistory_dir, 
                        rev_initial, rev_final)
            stdout, stderr = bb.process.run(cmd_full)
            if stdout and os.path.exists(self.workdir):
                with open(os.path.join(self.workdir, "buildhistory-diff-full.txt"),
                        "w+") as log:
                    log.write(stdout)
        except bb.process.ExecutionError as e:
            W( "%s: Buildhistory checking fails\n%s" % (self.pn, e.stdout))
