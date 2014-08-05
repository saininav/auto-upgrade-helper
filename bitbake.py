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
from errors import *

for path in os.environ["PATH"].split(':'):
    if os.path.exists(path) and "bitbake" in os.listdir(path):
        sys.path.insert(0, os.path.join(path, "../lib"))
        import bb

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

    def get_stdout_log(self):
        return os.path.join(self.log_dir, "bitbake_log.txt")

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

class BuildHistory(object):
    def __init__(self, build_dir):
        self.build_dir = build_dir
        self.work_dir = None

    def set_work_dir(self, work_dir):
        self.work_dir = work_dir

    # Return True if buildhistory-diff gives output
    def diff(self, revision_steps):
        os.chdir(self.build_dir)
        cmd = "buildhistory-diff HEAD~" + str(revision_steps)

        try:
            stdout, stderr = bb.process.run(cmd)
            # Write diff output to log file if there is any

            if stdout and os.path.exists(self.work_dir):
                with open(os.path.join(self.work_dir, "buildhistory.txt"), "w+") as log:
                    log.write(stdout)
                return True
        except bb.process.ExecutionError as e:
            for line in e.stdout.split('\n'):
                if line.find("Buildhistory directory \"buildhistory/\" does not exist") == 0:
                    C(" \"buildhistory.bbclass\" not inherited. Consider adding "
                      "the following to your local.conf:\n\n"
                      "INHERIT =+ \"buildhistory\"\n"
                      "BUILDHISTORY_COMMIT = \"1\"\n\n"
                      "Do not remove any other inherited class in the process (e.g. distrodata)\n")
                    exit(1)

        return False

