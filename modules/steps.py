#!/usr/bin/env python
# vim: set ts=4 sw=4 et:
#
# Copyright (c) 2013 - 2015 Intel Corporation
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
import sys

from logging import debug as D
from logging import info as I
from logging import warning as W
from logging import error as E
from logging import critical as C

from errors import *
from buildhistory import BuildHistory

from recipe.base import Recipe
from recipe.git import GitRecipe
from recipe.svn import SvnRecipe

def load_env(bb, git, opts, pkg_ctx):
    stdout = git.status()
    if stdout != "":
        if opts['interactive']:
            W(" %s: git repository has uncommited work which will be dropped!" \
                    " Proceed? (y/N)" % pkg_ctx['PN'])
            answer = sys.stdin.readline().strip().upper()
            if answer == '' or answer != 'Y':
                I(" %s: User abort!" % pkg_ctx['PN'])
                exit(1)

        I(" %s: Dropping uncommited work!" % pkg_ctx['PN'])
        git.reset_hard()
        git.clean_untracked()

    pkg_ctx['env'] = bb.env(pkg_ctx['PN'])
    pkg_ctx['workdir'] = os.path.join(pkg_ctx['base_dir'], pkg_ctx['PN'])
    os.mkdir(pkg_ctx['workdir'])
    pkg_ctx['recipe_dir'] = os.path.dirname(pkg_ctx['env']['FILE'])

    if pkg_ctx['env']['PV'] == pkg_ctx['NPV']:
        raise UpgradeNotNeededError

def clean_repo(bb, git, opts, pkg_ctx):
    try:
        git.checkout_branch("upgrades")
    except Error:
        git.create_branch("upgrades")

    try:
        git.delete_branch("remove_patches")
    except:
        pass

def detect_recipe_type(bb, git, opts, pkg_ctx):
    if pkg_ctx['env']['SRC_URI'].find("ftp://") != -1 or  \
            pkg_ctx['env']['SRC_URI'].find("http://") != -1 or \
            pkg_ctx['env']['SRC_URI'].find("https://") != -1:
        recipe = Recipe
    elif pkg_ctx['env']['SRC_URI'].find("git://") != -1:
        recipe = GitRecipe
    else:
        raise UnsupportedProtocolError

    pkg_ctx['recipe'] = recipe(pkg_ctx['env'], pkg_ctx['NPV'],
            opts['interactive'], pkg_ctx['workdir'],
            pkg_ctx['recipe_dir'], bb, git)

def buildhistory_init(bb, git, opts, pkg_ctx):
    if not opts['buildhistory']:
        return

    pkg_ctx['buildhistory'] = BuildHistory(bb, pkg_ctx['PN'],
            pkg_ctx['workdir'])
    I(" %s: Initial buildhistory for %s ..." % (pkg_ctx['PN'],
            opts['machines']))
    pkg_ctx['buildhistory'].init(opts['machines'])

def unpack_original(bb, git, opts, pkg_ctx):
    pkg_ctx['recipe'].unpack()

def rename(bb, git, opts, pkg_ctx):
    pkg_ctx['recipe'].rename()

    pkg_ctx['env'] = bb.env(pkg_ctx['PN'])

    pkg_ctx['recipe'].update_env(pkg_ctx['env'])

def cleanall(bb, git, opts, pkg_ctx):
    pkg_ctx['recipe'].cleanall()

def fetch(bb, git, opts, pkg_ctx):
    pkg_ctx['recipe'].fetch()

def compile(bb, git, opts, pkg_ctx):
    if opts['skip_compilation']:
        W(" %s: Compilation was skipped by user choice!")
        return

    for machine in opts['machines']:
        I(" %s: compiling for %s ..." % (pkg_ctx['PN'], machine))
        pkg_ctx['recipe'].compile(machine)
        if opts['buildhistory']:
            pkg_ctx['buildhistory'].add()

def buildhistory_diff(bb, git, opts, pkg_ctx):
    if not opts['buildhistory']:
        return

    I(" %s: Checking buildhistory ..." % pkg_ctx['PN'])
    pkg_ctx['buildhistory'].diff()

upgrade_steps = [
    (load_env, "Loading environment ..."),
    (clean_repo, "Cleaning git repository of temporary branch ..."),
    (detect_recipe_type, None),
    (buildhistory_init, None),
    (unpack_original, "Fetch & unpack original version ..."),
    (rename, "Renaming recipes, reset PR (if exists) ..."),
    (cleanall, "Clean all ..."),
    (fetch, "Fetch new version (old checksums) ..."),
    (compile, None),
    (buildhistory_diff, None)
]
