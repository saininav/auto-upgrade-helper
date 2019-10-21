# SPDX-License-Identifier: GPL-2.0-or-later
import os
import logging as log
from logging import debug as D

from utils.bitbake import *

class Devtool(object):
    def __init__(self):
        super(Devtool, self).__init__()

    def _cmd(self, operation):
        cmd = "devtool " + operation
        try:
            D("Running '%s'" %(cmd))
            stdout, stderr = bb.process.run(cmd)
        except bb.process.ExecutionError as e:
            D("%s returned:\n%s" % (cmd, e.__str__()))
            raise DevtoolError("The following devtool command failed: " + operation,
                        e.stdout, e.stderr)

        return stdout

    def upgrade(self, recipe, version = None, revision = None):
        cmd = " upgrade " + recipe
        if version and not version.endswith("-new-commits-available"):
            cmd = cmd + " -V " + version
        if revision and revision != "N/A":
            cmd = cmd + " -S " + revision
        return self._cmd(cmd)

    def finish(self, recipe, layer):
        cmd = " finish -f " + recipe + " " + layer
        return self._cmd(cmd)

    def reset(self, recipe = None):
        if recipe:
            cmd = " reset -n " + recipe
        else:
            cmd = " reset -a"
        return self._cmd(cmd)

