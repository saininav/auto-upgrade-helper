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
import shutil
import re

from logging import debug as D
from logging import info as I
from logging import warning as W
from logging import error as E
from logging import critical as C

from errors import *
from buildhistory import BuildHistory

def load_env(devtool, bb, git, opts, pkg_ctx):
    pkg_ctx['env'] = bb.env(pkg_ctx['PN'])
    pkg_ctx['workdir'] = os.path.join(pkg_ctx['base_dir'], pkg_ctx['PN'])
    os.mkdir(pkg_ctx['workdir'])
    pkg_ctx['recipe_dir'] = os.path.dirname(pkg_ctx['env']['FILE'])

    if pkg_ctx['env']['PV'] == pkg_ctx['NPV']:
        raise UpgradeNotNeededError

def buildhistory_init(devtool, bb, git, opts, pkg_ctx):
    if not opts['buildhistory']:
        return

    pkg_ctx['buildhistory'] = BuildHistory(bb, pkg_ctx['PN'],
            pkg_ctx['workdir'])
    I(" %s: Initial buildhistory for %s ..." % (pkg_ctx['PN'],
            opts['machines'][:1]))
    pkg_ctx['buildhistory'].init(opts['machines'][:1])

def _extract_license_diff(devtool_output):
    licenseinfo = []
    for line in devtool_output.split('\n'):
        if line.startswith("NOTE: New recipe is"):
            recipepath = line.split()[4]
            with open(recipepath, 'rb') as f:
                lines = f.readlines()

            extracting = False
            with open(recipepath, 'wb') as f:
                for line in lines:
                     if line.startswith(b'# FIXME: the LIC_FILES_CHKSUM'):
                         extracting = True
                     elif extracting == True and not line.startswith(b'#') and len(line) > 1:
                         extracting = False
                     if extracting == True:
                         licenseinfo.append(line[2:])
                     else:
                         f.write(line)
    D(" License diff extracted: {}".format(b"".join(licenseinfo).decode('utf-8')))
    return licenseinfo

def devtool_upgrade(devtool, bb, git, opts, pkg_ctx):
    if pkg_ctx['NPV'].endswith("new-commits-available"):
        pkg_ctx['commit_msg'] = "{}: upgrade to latest revision".format(pkg_ctx['PN'])
    else:
        pkg_ctx['commit_msg'] = "{}: upgrade {} -> {}".format(pkg_ctx['PN'], pkg_ctx['PV'], pkg_ctx['NPV'])

    try:
        devtool_output = devtool.upgrade(pkg_ctx['PN'], pkg_ctx['NPV'], pkg_ctx['NSRCREV'])
    except DevtoolError as e1:
        try:
            devtool_output = devtool.reset(pkg_ctx['PN'])
            _rm_source_tree(devtool_output)
        except DevtoolError as e2:
            pass
        raise e1

    license_diff_info = _extract_license_diff(devtool_output)
    if len(license_diff_info) > 0:
        pkg_ctx['license_diff_fn'] = "license-diff.txt"
        with open(os.path.join(pkg_ctx['workdir'], pkg_ctx['license_diff_fn']), 'wb') as f:
            f.write(b"".join(license_diff_info))

    D(" 'devtool upgrade' printed:\n%s" %(devtool_output))

def _compile(bb, pkg, machine, workdir):
        try:
            bb.complete(pkg, machine)
        except Error as e:
            with open("{}/bitbake-output-{}.txt".format(workdir, machine), 'w') as f:
                f.write(e.stdout)
            for line in e.stdout.split("\n"):
                # version going backwards is not a real error
                if re.match(".* went backwards which would break package feeds .*", line):
                    break
            else:
                raise CompilationError()

def compile(devtool, bb, git, opts, pkg_ctx):
    if opts['skip_compilation']:
        W(" %s: Compilation was skipped by user choice!" % pkg_ctx['PN'])
        return

    for machine in opts['machines']:
        I(" %s: compiling upgraded version for %s ..." % (pkg_ctx['PN'], machine))
        _compile(bb, pkg_ctx['PN'], machine, pkg_ctx['workdir'])
        if opts['buildhistory']:
            pkg_ctx['buildhistory'].add()

def buildhistory_diff(devtool, bb, git, opts, pkg_ctx):
    if not opts['buildhistory']:
        return

    I(" %s: Checking buildhistory ..." % pkg_ctx['PN'])
    pkg_ctx['buildhistory'].diff()

def _rm_source_tree(devtool_output):
    for line in devtool_output.split("\n"):
        if line.startswith("NOTE: Leaving source tree"):
            srctree = line.split()[4]
            shutil.rmtree(srctree)

def devtool_finish(devtool, bb, git, opts, pkg_ctx):
    try:
        devtool_output = devtool.finish(pkg_ctx['PN'], pkg_ctx['recipe_dir'])
        _rm_source_tree(devtool_output)
        D(" 'devtool finish' printed:\n%s" %(devtool_output))
    except DevtoolError as e1:
        try:
            devtool_output = devtool.reset(pkg_ctx['PN'])
            _rm_source_tree(devtool_output)
        except DevtoolError as e2:
            pass
        raise e1

upgrade_steps = [
    (load_env, "Loading environment ..."),
    (buildhistory_init, None),
    (devtool_upgrade, "Running 'devtool upgrade' ..."),
    (devtool_finish, "Running 'devtool finish' ..."),
    (compile, None),
    (buildhistory_diff, None),
]
