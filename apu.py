#!/usr/bin/env python

import argparse
import os
import logging as log
from logging import debug as D
from logging import info as I
from logging import warning as W
from logging import error as E
from logging import critical as C
import re
import sys

for path in os.environ["PATH"].split(':'):
    if os.path.exists(path) and "bitbake" in os.listdir(path):
        sys.path.insert(0, os.path.join(path, "../lib"))
        import bb


def get_build_dir():
    return os.getenv('BUILDDIR')


class Git():
    def __init__(self, dir):
        self.repo_dir = dir

    def _cmd(self, operation):
        os.chdir(self.repo_dir)

        cmd = "git " + operation
        try:
            stdout, stderr = bb.process.run(cmd)
        except bb.process.ExecutionError as ex:
            D("%s returned:\n%s" % (cmd, ex.__str__()))
            return (-1, None, None)

        return (0, stdout, stderr)

    def mv(self, src, dest):
        return self._cmd("mv " + src + " " + dest)

    def stash(self):
        return self._cmd("stash")


class Bitbake():
    def __init__(self, build_dir):
        self.build_dir = build_dir

    def _cmd(self, recipe, options=None):
        cmd = "bitbake "
        if options is not None:
            cmd += options + " "

        os.chdir(self.build_dir)

        try:
            stdout, stderr = bb.process.run(cmd + recipe)
        except bb.process.ExecutionError as ex:
            D("%s returned:\n%s" % (cmd, ex.__str__()))
            return (-1, None, None)

        return (0, stdout, stderr)

    def env(self, recipe):
        return self._cmd(recipe, "-e")

    def fetch(self, recipe):
        return self._cmd(recipe, "-c fetch")

    def checkpkg(self, recipe):
        return self._cmd(recipe, "-c checkpkg")

    def complete(self, recipe):
        return self._cmd(recipe)


class Package():
    def __init__(self, package_name, to_ver=None):
        self.pn = package_name
        self.to_ver = to_ver
        self.bb = Bitbake(get_build_dir())

    def env(self):
        I(" %s: Fetching package environment..." % self.pn)

        err, stdout, stderr = self.bb.env(self.pn)
        if err == -1:
            return None

        assignment = re.compile("^([^ \t]*)=(.*)")
        bb_env = dict()
        for line in stdout.split('\n'):
            m = assignment.match(line)
            if m:
                if m.group(1) in bb_env:
                    continue

                bb_env[m.group(1)] = m.group(2).strip("\"")

        return bb_env

    def next_version(self):
        I(" %s: Checking next available version..." % self.pn)

        err = self.bb.checkpkg(self.pn)
        if err == -1:
            return None

        csv = open(get_build_dir() + "/tmp/log/checkpkg.csv", "r")
        new_ver = csv.readlines()[1].split()[2]
        csv.close()

        return new_ver

    def src_uri_supported(self):
        if self.env['SRC_URI'].find("ftp://") == 0 or  \
                self.env['SRC_URI'].find("http://") == 0 or \
                self.env['SRC_URI'].find("https://") == 0:
            return True

        return False

    def rename_files(self):
        for path in os.listdir(self.recipe_dir):
            if path.find(self.env['PN'] + '-' + self.env['PKGV']) != -1 or \
                    path.find(self.env['PN'] + '_' + self.env['PKGV']) != -1:
                new_path = re.sub(self.env['PKGV'], self.to_ver, path)
                if self.git.mv(path, new_path) == (-1,):
                    E(" %s: Rename operation failed!" % self.pn)
                    return -1

        return 0

    def last_executed_task(self):
        with open(os.path.realpath(self.env["T"] + "/log.task_order")) as task_order:
            last_line = list(task_order)[-1]
            m = re.match("^(.*) \(.*\): (.*)$", last_line)
            if m:
                return (m.group(1), m.group(2))

        return (None, None)

    def replace_checksums(self):
        task, fetch_log = self.last_executed_task()
        if fetch_log is None:
            E(" %s: Could not get the fetch log name!" % self.pn)
            return -1

        md5sum_line = None
        sha256sum_line = None
        with open(os.path.realpath(self.env["T"] + "/" + fetch_log)) as log:
            for line in log:
                print("%s" % line)
                if line.startswith("SRC_URI[md5sum]"):
                    md5sum_line = line
                elif line.startswith("SRC_URI[sha256sum]"):
                    sha256sum_line = line

        if md5sum_line is None or sha256sum_line is None:
            E(" %s: Could not extract the new checksums from log file!" % self.pn)
            return -1

        with open(self.env['FILE'] + ".tmp", "rw+") as temp_recipe:
            with open(self.env['FILE']) as recipe:
                for line in recipe:
                    if line.startswith("SRC_URI[md5sum]"):
                        temp_recipe.write(md5sum_line)
                    elif line.startswith("SRC_URI[sha256sum]"):
                        temp_recipe.write(sha256sum_line)
                    else:
                        temp_recipe.write(line)

        return 0

    def upgrade(self):
        if self.to_ver is None:
            self.to_ver = self.next_version()

        if self.to_ver is None:
            E(" %s: Could not determine the next version!" % self.pn)
            return -1

        I(" %s: Upgrading to version %s ..." % (self.pn, self.to_ver))

        self.env = self.env()
        if self.env is None:
            E(" %s: Could not fetch package environment!" % self.pn)
            return -1

        if not self.src_uri_supported():
            E(" %s: Unsupported SRC_URI protocol!" % self.pn)
            return -1

        self.recipe_dir = os.path.dirname(self.env['FILE'])

        self.git = Git(self.recipe_dir)

        I(" %s: Stash uncommited work, if any ..." % self.pn)
        if self.git.stash() == (-1,):
            E(" %s: Stash failed!" % self.pn)
            return -1

        I(" %s: Fetch original package ..." % self.pn)
        if self.bb.fetch(self.pn) == (-1,):
            E(" %s: Fetching original package failed!" % self.pn)
            return -1

        I(" %s: Renaming files ..." % self.pn)
        if self.rename_files() == -1:
            return -1

        I(" %s: Fetch new package (old checksums) ..." % self.pn)
        if self.bb.fetch(self.pn) == (0,):
            E(" %s: Fetching new package (old checksums) succeeded! Should've failed!" % self.pn)
            return -1

        I(" %s: Update recipe checksums, remove PR, etc ..." % self.pn)
        if self.replace_checksums() == -1:
            E(" %s: Could not change recipe!" % self.pn)
            return -1


#[lp]class List(Package):
#[lp]    def __init(self, package_list):
#[lp]
#[lp]
#[lp]class Universe(List):
#[lp]    def __init__(self):
#[lp]        self.get_packages_to_upgrade()


def parse_cmdline():
    parser = argparse.ArgumentParser(description='Auto Upgrade Packages')
    parser.add_argument("package", help="package to be upgraded")
    parser.add_argument("-t", "--to_version",
                        help="version to upgrade the package to")
    parser.add_argument("-m", "--send_mail",
                        help="send mail when finished", action="store_true")
    parser.add_argument("-d", "--debug-level", type=int, default=4, choices=range(1, 6),
                        help="set the debug level: CRITICAL=1, ERROR=2, WARNING=3, INFO=4, DEBUG=5")
    return parser.parse_args()


#[lp]def bb_exec_cmd(recipe, cmd=None, options=None):
#[lp]    os.chdir(get_build_dir())
#[lp]    bb_cmd = "bitbake"
#[lp]    if cmd:
#[lp]        bb_cmd += " -c " + cmd
#[lp]    if options:
#[lp]        bb_cmd += ' ' + options
#[lp]    bb_cmd += ' ' + recipe
#[lp]
#[lp]    D(" Executing: %s" % bb_cmd)
#[lp]    try:
#[lp]        bb.process.run(bb_cmd)
#[lp]    except bb.process.ExecutionError as ex:
#[lp]        D("%s" % ex.__str__())
#[lp]        return -1
#[lp]
#[lp]    return 0

#[lp]def bb_fetch(recipe):
#[lp]    I(" Fetching %s ..." % recipe)
#[lp]    if bb_exec_cmd(recipe, "fetch"):
#[lp]        return -1
#[lp]
#[lp]    return 0
#[lp]
#[lp]def bb_compile(recipe):
#[lp]    I(" Compiling %s ..." % recipe)
#[lp]    if bb_exec_cmd(recipe, "compile"):
#[lp]        return -1
#[lp]
#[lp]    return 0
#[lp]
#[lp]def bb_get_failed_task(recipe):
#[lp]    global bb_env
#[lp]    with open(os.path.realpath(bb_env["T"] + "/log.task_order")) as task_order:
#[lp]        last_line = list(task_order)[-1]
#[lp]        m=re.match("^(.*) \(.*\): (.*)$", last_line)
#[lp]        if m:
#[lp]            return (m.group(1), m.group(2))
#[lp]
#[lp]    return (None, None)

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

#[lp]def get_next_version(pkg):
#[lp]    I(" Checking next available version...")
#[lp]    os.chdir(get_build_dir())
#[lp]    bb.process.run("bitbake -c checkpkg " + pkg)
#[lp]
#[lp]    csv = open(get_build_dir() + "/tmp/log/checkpkg.csv", "r")
#[lp]    new_ver = csv.readlines()[1].split()[2]
#[lp]    csv.close()
#[lp]
#[lp]    return new_ver

#[lp]def get_bb_env(pkg):
#[lp]    I(" Fetching package environment...")
#[lp]    try:
#[lp]        stdout, stderr = bb.process.run("bitbake -e " +pkg)
#[lp]        assignment = re.compile("^([^ \t]*)=(.*)")
#[lp]        bb_env = dict()
#[lp]        for line in stdout.split('\n'):
#[lp]            m = assignment.match(line)
#[lp]            if m:
#[lp]                if m.group(1) in bb_env:
#[lp]                    continue
#[lp]
#[lp]                bb_env[m.group(1)] = m.group(2).strip("\"")
#[lp]
#[lp]    except bb.process.ExecutionError as ex:
#[lp]        D("%s" % ex.__str__())
#[lp]        return None
#[lp]
#[lp]    return bb_env

#[lp]def git_cmd(operation):
#[lp]    global bb_env
#[lp]    os.chdir(os.path.dirname(bb_env['FILE']))
#[lp]
#[lp]    cmd = "git " + operation
#[lp]    try:
#[lp]        stdout, stderr = bb.process.run(cmd)
#[lp]    except bb.process.ExecutionError as ex:
#[lp]        D("%s returned\n%s" % (git_cmd, ex.__str__()))
#[lp]        return (-1, None, None)
#[lp]
#[lp]    return (0, stdout, stderr)
#[lp]
#[lp]def repo_is_clean():
#[lp]    I(" Check if there is uncommited work ...")
#[lp]    ret, stdout, stderr  = git_cmd("status --porcelain")
#[lp]    if ret < 0:
#[lp]        E(" Could not check if repo is clean ...")
#[lp]        return -1
#[lp]
#[lp]    if stdout != "":
#[lp]        D(" git status returned:\n%s" % stdout)
#[lp]        return 0
#[lp]
#[lp]    return 1
#[lp]
#[lp]def stash_uncommited_work():
#[lp]    I(" Stash uncommited work ...")
#[lp]    ret = bb.process.run("git stash")
#[lp]    if ret < 0:
#[lp]        E(" Could not stash uncommited work ...")
#[lp]        return -1
#[lp]
#[lp]    return 0
#[lp]
#[lp]def create_work_branch():
#[lp]    I(" Create new upgrade branch ...")
#[lp]    ret = bb.process.run("git checkout master")
#[lp]    ret = bb.process.run("git checkout -b upgrades_apu")
#[lp]    if ret < 0:
#[lp]        E(" Could not create \"upgrades\" branch ...")
#[lp]        return -1
#[lp]
#[lp]    return 0
#[lp]
#[lp]def update_recipe ():
#[lp]    global bb_env
#[lp]
#[lp]    tmp_recipe = open(bb_env['FILE'] + ".tmp", 'w+')
#[lp]    with open(bb_env['FILE'], 'r') as recipe:
#[lp]        for line in list(recipe):
#[lp]
#[lp]
#[lp]    close(tmp_recipe)
#[lp]
#[lp]def move_to_next_ver():
#[lp]    global bb_env
#[lp]
#[lp]    # move the recipe(s) to the next version
#[lp]    recipe_dir = os.path.dirname(bb_env['FILE'])
#[lp]    os.chdir(recipe_dir)
#[lp]    if bb_env['SRC_URI'].find("ftp://") == 0 or  \
#[lp]       bb_env['SRC_URI'].find("http://") == 0 or \
#[lp]       bb_env['SRC_URI'].find("https://") == 0:
#[lp]        for path in os.listdir(recipe_dir):
#[lp]            if path.find(bb_env['PN'] + '-' + bb_env['PKGV']) != -1 or \
#[lp]               path.find(bb_env['PN'] + '_' + bb_env['PKGV']) != -1:
#[lp]                new_path=re.sub(bb_env['PKGV'], new_ver, path)
#[lp]                ret = git_cmd("mv " + path + " " + new_path)
#[lp]                if ret < 0:
#[lp]                    E(" Rename operation failed!")
#[lp]                    return -1
#[lp]    else:
#[lp]        E("Recipe SRC_URI not supported")
#[lp]        return -1
#[lp]
#[lp]    return 0
#[lp]
#[lp]def upgrade(pkg, new_ver=None):
#[lp]    global bb_env
#[lp]
#[lp]    if new_ver:
#[lp]        I(" Upgrade package '%s' to version %s" % (pkg, ver))
#[lp]    else:
#[lp]        I(" Upgrade package '%s' to next available version" % pkg)
#[lp]
#[lp]        new_ver = get_next_version(pkg)
#[lp]
#[lp]    if new_ver == bb_env['PKGV']:
#[lp]        I(" No need to upgrade: next version and current version coincide: %s!" % new_ver)
#[lp]        return 0
#[lp]    else:
#[lp]        I(" Upgrade %s from %s to %s" % (pkg, bb_env['PKGV'], new_ver))
#[lp]
#[lp]    # fetch the original package
#[lp]    if bb_fetch(pkg) == -1:
#[lp]        E(" Failed to fetch the original version of the package: %s!" % bb_env['PKGV'])
#[lp]        return -1
#[lp]
#[lp]    # replace md5sum and sha256sum in recipe
#[lp]    move_to_next_ver()
#[lp]
#[lp]    # fetch the new version. This MUST fail
#[lp]    if bb_fetch(pkg) != -1:
#[lp]        E(" Fetching the new version should fail!")
#[lp]        return -1
#[lp]
#[lp]
#[lp]    return 0

if __name__ == "__main__":
    debug_levels = [log.CRITICAL, log.ERROR, log.WARNING, log.INFO, log.DEBUG]
    args = parse_cmdline()
    log.basicConfig(format='%(levelname)s:%(message)s',
                    level=debug_levels[args.debug_level - 1])

    if not os.getenv('BUILDDIR', False):
        E(" You must source oe-init-build-env before running this script!\n")
        exit(1)

    pkg = Package("git")
    pkg.upgrade()

#[lp]    bb_env = get_bb_env(args.package)
#[lp]    if bb_env == None:
#[lp]        exit(1)
#[lp]
#[lp]    if bb_env['INHERIT'].find("distrodata") == -1:
#[lp]        E(" \"distrodata.bbclass\" not inherited. Consider adding the following to your local.conf:\n"\
#[lp]          "INHERIT =+ \"distrodata\"\n"\
#[lp]          "require conf/distro/include/recipe_color.inc\n"\
#[lp]          "require conf/distro/include/distro_alias.inc\n"\
#[lp]          "require conf/distro/include/maintainers.inc\n"\
#[lp]          "require conf/distro/include/upstream_tracking.inc\n")
#[lp]        exit(1)
#[lp]
#[lp]    os.chdir(os.path.dirname(bb_env['FILE']))
#[lp]
#[lp]    if not repo_is_clean():
#[lp]        stash_uncommited_work()
#[lp]
#[lp]#[lp]    upgrade(args.package, args.to_version)
#[lp]    update_recipe()
