#!/usr/bin/env python

import argparse
import os
import subprocess as sp
import logging as log
from logging import debug    as D
from logging import info     as I
from logging import warning  as W
from logging import error    as E
from logging import critical as C
import re
import sys

for path in os.environ["PATH"].split(':'):
    if os.path.exists(path) and "bitbake" in os.listdir(path):
        sys.path.insert(0, os.path.join(path, "../lib"))
        import bb


def parse_cmdline():
    parser = argparse.ArgumentParser(description='Auto Upgrade Packages')
    parser.add_argument("package", help="package to be upgraded")
    parser.add_argument("-t", "--to_version",
                            help="version to upgrade the package to")
    parser.add_argument("-m", "--send_mail",
                            help="send mail when finished", action="store_true")
    parser.add_argument("-d", "--debug-level", type=int, default=4, choices=range(1,6),
                            help="set the debug level: CRITICAL=1, ERROR=2, WARNING=3, INFO=4, DEBUG=5")
    return parser.parse_args()

def get_build_dir():
    return os.getenv('BUILDDIR')

def bb_exec_cmd(recipe, cmd=None, options=None):
    os.chdir(get_build_dir())
    bb_cmd = "bitbake"
    if cmd:
        bb_cmd += " -c " + cmd
    if options:
        bb_cmd += ' ' + options
    bb_cmd += ' ' + recipe

    D(" Executing: %s" % bb_cmd)
    try:
        bb.process.run(bb_cmd)
    except bb.process.ExecutionError as ex:
        D("%s" % ex.__str__())
        return -1

    return 0

def bb_fetch(recipe):
    I(" Fetching %s ..." % recipe)
    if bb_exec_cmd(recipe, "fetch"):
        return -1

    return 0

def bb_compile(recipe):
    I(" Compiling %s ..." % recipe)
    if bb_exec_cmd(recipe, "compile"):
        return -1

    return 0

def bb_get_failed_task(recipe):
    global bb_env
    with open(os.path.realpath(bb_env["T"] + "/log.task_order")) as task_order:
        last_line = list(task_order)[-1]
        m=re.match("^(.*) \(.*\): (.*)$", last_line)
        if m:
            return (m.group(1), m.group(2))

    return (None, None)

#[lp]def bb(recipe):
#[lp]    global bb_env
#[lp]
#[lp]    print("* Compiling %s ..." % recipe)
#[lp]    if bb_exec_cmd(recipe):
#[lp]        (task, log_file) = bb_get_failed_task(recipe)
#[lp]        log.error(" Task %s failed! Check log for details: %s" % (task, os.path.realpath(bb_env["T"] + "/" + log_file)))
#[lp]        return True
#[lp]
#[lp]    return False

def get_next_version(pkg):
    I(" Checking next available version...")
    os.chdir(get_build_dir())
    bb.process.run("bitbake -c checkpkg " + pkg)

    csv = open(get_build_dir() + "/tmp/log/checkpkg.csv", "r")
    new_ver = csv.readlines()[1].split()[2]
    csv.close()

    return new_ver

def get_bb_env(pkg):
    I(" Fetching package environment...")
    try:
        stdout, stderr = bb.process.run("bitbake -e " +pkg)
        assignment = re.compile("^([^ \t]*)=(.*)")
        bb_env = dict()
        for line in stdout.split('\n'):
            m = assignment.match(line)
            if m:
                if m.group(1) in bb_env:
                    continue

                bb_env[m.group(1)] = m.group(2).strip("\"")

    except bb.process.ExecutionError as ex:
        D("%s" % ex.__str__())
        return None

    return bb_env

def git_cmd(operation):
    global bb_env
    os.chdir(os.path.dirname(bb_env['FILE']))

    cmd = "git " + operation
    try:
        stdout, stderr = bb.process.run(cmd)
    except bb.process.ExecutionError as ex:
        D("%s returned\n%s" % (git_cmd, ex.__str__()))
        return (-1, None, None)

    return (0, stdout, stderr)

def repo_is_clean():
    I(" Check if there is uncommited work ...")
    ret, stdout, stderr  = git_cmd("status --porcelain")
    if ret < 0:
        E(" Could not check if repo is clean ...")
        return -1

    if stdout != "":
        D(" git status returned:\n%s" % stdout)
        return 0

    return 1

def stash_uncommited_work():
    I(" Stash uncommited work ...")
    ret = bb.process.run("git stash")
    if ret < 0:
        E(" Could not stash uncommited work ...")
        return -1

    return 0

def create_work_branch():
    I(" Create new upgrade branch ...")
    ret = bb.process.run("git checkout master")
    ret = bb.process.run("git checkout -b upgrades")
    if ret < 0:
        E(" Could not create \"upgrades\" branch ...")
        return -1

    return 0

def move_to_next_ver():
    global bb_env

    # move the recipe(s) to the next version
    recipe_dir = os.path.dirname(bb_env['FILE'])
    os.chdir(recipe_dir)
    if bb_env['SRC_URI'].find("ftp://") == 0 or  \
       bb_env['SRC_URI'].find("http://") == 0 or \
       bb_env['SRC_URI'].find("https://") == 0:
        for path in os.listdir(recipe_dir):
            if path.find(bb_env['PN'] + '-' + bb_env['PKGV']) != -1 or \
               path.find(bb_env['PN'] + '_' + bb_env['PKGV']) != -1:
                new_path=re.sub(bb_env['PKGV'], new_ver, path)
                ret = git_cmd("mv " + path + " " + new_path)
                if ret < 0:
                    E(" Rename operation failed!")
                    return -1
    else:
        return -1

    return 0

def upgrade(pkg, new_ver=None):
    global bb_env

    if new_ver:
        I(" Upgrade package '%s' to version %s" % (pkg, ver))
    else:
        I(" Upgrade package '%s' to next available version" % pkg)

        new_ver = get_next_version(pkg)

    if new_ver == bb_env['PKGV']:
        I(" No need to upgrade: next version and current version coincide: %s!" % new_ver)
        return 0
    else:
        I(" Upgrade %s from %s to %s" % (pkg, bb_env['PKGV'], new_ver))

    # fetch the original package
    if bb_fetch(pkg) == -1:
        E(" Failed to fetch the original version of the package: %s!" % bb_env['PKGV'])
        return -1


    # fetch the new version. This MUST fail
    if bb_fetch(pkg) != -1:
        E(" Fetching the new version should fail!")
        return -1

    # replace md5sum and sha256sum in recipe
    move_to_next_ver()

    return 0

if __name__ == "__main__":
    debug_levels = [log.CRITICAL, log.ERROR, log.WARNING, log.INFO, log.DEBUG]
    args = parse_cmdline()
    log.basicConfig(format='%(levelname)s:%(message)s',
                            level=debug_levels[args.debug_level - 1])

    if not os.getenv('BUILDDIR', False):
        E(" You must source oe-init-build-env before running this script!\n")
        exit(1)

    bb_env = get_bb_env(args.package)
    if bb_env == None:
        exit(1)

    if bb_env['INHERIT'].find("distrodata") == -1:
        E(" \"distrodata.bbclass\" not inherited. Consider adding the following to your local.conf:\n"\
          "INHERIT =+ \"distrodata\"\n"\
          "require conf/distro/include/recipe_color.inc\n"\
          "require conf/distro/include/distro_alias.inc\n"\
          "require conf/distro/include/maintainers.inc\n"\
          "require conf/distro/include/upstream_tracking.inc\n")
        exit(1)

    os.chdir(os.path.dirname(bb_env['FILE']))

    if not repo_is_clean():
        stash_uncommited_work()

    upgrade(args.package, args.to_version)

