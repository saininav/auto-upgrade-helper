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
# AUTHORS
# Laurentiu Palcu   <laurentiu.palcu@intel.com>
# Marius Avram      <marius.avram@intel.com>
#

import os
import logging as log
from logging import info as I
from logging import debug as D
from logging import error as E
from logging import critical as C
import sys
import re

from errors import *

for path in os.environ["PATH"].split(':'):
    if os.path.exists(path) and "bitbake" in os.listdir(path):
        sys.path.insert(0, os.path.join(path, "../lib"))
        import bb

BITBAKE_ERROR_LOG = 'bitbake_error_log.txt'

def get_build_dir():
    return os.getenv('BUILDDIR')

class Bitbake(object):
    def __init__(self, build_dir):
        self.build_dir = build_dir
        self.log_dir = None
        super(Bitbake, self).__init__()

    def _cmd(self, recipe=None, options=None, env_var=None, output_filter=None):
        cmd = ""
        if env_var is not None:
            cmd += env_var + " "
        cmd += "bitbake "
        if options is not None:
            cmd += options + " "

        if recipe is not None:
            cmd += recipe

        if output_filter is not None:
            cmd += ' |  grep ' + output_filter

        os.chdir(self.build_dir)

        try:
            stdout, stderr = bb.process.run(cmd)
        except bb.process.ExecutionError as e:
            D("%s returned:\n%s" % (cmd, e.__str__()))

            if self.log_dir is not None and os.path.exists(self.log_dir):
                with open(os.path.join(self.log_dir, BITBAKE_ERROR_LOG), "a+") as log:
                    log.write(e.stdout)

            raise Error("\'" + cmd + "\' failed", e.stdout, e.stderr)

        return stdout

    def set_log_dir(self, dir):
        self.log_dir = dir

    def get_stdout_log(self):
        return os.path.join(self.log_dir, BITBAKE_ERROR_LOG)

    def env(self, recipe=None):
        stdout = self._cmd(recipe, "-e", output_filter="-v \"^#\"")

        assignment = re.compile("^([^ \t=]*)=(.*)")
        bb_env = dict()
        for line in stdout.split('\n'):
            m = assignment.match(line)
            if m:
                if m.group(1) in bb_env:
                    continue

                bb_env[m.group(1)] = m.group(2).strip("\"")

        if not bb_env:
            raise EmptyEnvError(stdout)

        return bb_env

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
