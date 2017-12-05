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
from logging import debug as D

from utils.bitbake import *

class Git(object):
    def __init__(self, dir):
        self.repo_dir = dir
        super(Git, self).__init__()

    def _cmd(self, operation):
        os.chdir(self.repo_dir)

        cmd = "git " + operation
        try:
            stdout, stderr = bb.process.run(cmd)
        except bb.process.ExecutionError as e:
            D("%s executed from %s returned:\n%s" % (cmd, self.repo_dir, e.__str__()))
            raise Error("The following git command failed: " + operation,
                        e.stdout, e.stderr)

        return stdout

    def mv(self, src, dest):
        return self._cmd("mv -f " + src + " " + dest)

    def stash(self):
        return self._cmd("stash")

    def commit(self, commit_message, author=None):
        if author is None:
            return self._cmd("commit -a -s -m \"" + commit_message + "\"")
        else:
            return self._cmd("commit -a --author=\"" + author + "\" -m \"" + commit_message + "\"")

    def create_patch(self, out_dir):
        return self._cmd("format-patch -M10 -1 -o " + out_dir)

    def apply_patch(self, patch_file):
        return self._cmd("am %s" % patch_file)

    def abort_patch(self):
        return self._cmd("am --abort")

    def status(self):
        return self._cmd("status --porcelain")

    def checkout_branch(self, branch_name):
        return self._cmd("checkout " + branch_name)

    def create_branch(self, branch_name):
        return self._cmd("checkout -b " + branch_name)

    def delete_branch(self, branch_name):
        return self._cmd("branch -D " + branch_name)

    def pull(self):
        return self._cmd("pull")

    def reset_hard(self, no_of_patches=0):
        if no_of_patches == 0:
            return self._cmd("reset --hard HEAD")
        else:
            return self._cmd("reset --hard HEAD~" + str(no_of_patches))

    def reset_soft(self, no_of_patches):
        return self._cmd("reset --soft HEAD~" + str(no_of_patches))

    def clean_untracked(self):
        return self._cmd("clean -fd")

    def last_commit(self, branch_name):
        return self._cmd("log --pretty=format:\"%H\" -1 " + branch_name)

    def ls_remote(self, repo_url=None, options=None, refs=None):
        cmd = "ls-remote"
        if options is not None:
            cmd += " " + options
        if repo_url is not None:
            cmd += " " + repo_url
        if refs is not None:
            cmd += " " + refs
        return self._cmd(cmd)
