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
import subprocess

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

def clean_repo(bb, git, opts, pkg_ctx):
    git.checkout_branch("master")

    try:
        git.delete_branch("remove_patches")
    except:
        pass
    try:
        git.delete_branch("upgrades")
    except:
        pass

    git.reset_hard()
    git.create_branch("upgrades")

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

def pack_original_workdir(bb, git, opts, pkg_ctx):
    recipe_workdir = os.path.dirname(pkg_ctx['env']['S'])
    pkg_ctx['recipe_workdir_tarball'] = os.path.join(pkg_ctx['workdir'],
            'original_workdir.tar.gz')

    try:
        subprocess.call(["tar", "-chzf", pkg_ctx['recipe_workdir_tarball'],
                         recipe_workdir], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    except:
        W(" %s, Can't compress original workdir, if license diff" \
          " is needed will show full file." % pkg_ctx['PN'])

def rename(bb, git, opts, pkg_ctx):
    pkg_ctx['recipe'].rename()

    pkg_ctx['env'] = bb.env(pkg_ctx['PN'])

    pkg_ctx['recipe'].update_env(pkg_ctx['env'])

def cleanall(bb, git, opts, pkg_ctx):
    pkg_ctx['recipe'].cleanall()

def fetch(bb, git, opts, pkg_ctx):
    pkg_ctx['recipe'].fetch()

def unpack_original_workdir(bb, git, opts, pkg_ctx):
    try:
        subprocess.call(["tar", "-xhzf", pkg_ctx['recipe_workdir_tarball'],
                         "-C", "/"], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
        os.unlink(pkg_ctx['recipe_workdir_tarball'])
    except:
        pass

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
    (clean_repo, "Cleaning git repository of temporary branch ..."),
    (load_env, "Loading environment ..."),
    (detect_recipe_type, None),
    (buildhistory_init, None),
    (unpack_original, "Fetch & unpack original version ..."),
    (pack_original_workdir, None),
    (rename, "Renaming recipes, reset PR (if exists) ..."),
    (cleanall, "Clean all ..."),
    (fetch, "Fetch new version (old checksums) ..."),
    (unpack_original_workdir, None),
    (compile, None),
    (buildhistory_diff, None)
]
