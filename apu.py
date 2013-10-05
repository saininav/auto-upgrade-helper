#!/usr/bin/env python

import argparse
import os
import subprocess as sp
import logging as log
import re

try:
    import git
except ImportError:
    print("git module is not installed. Abort!")
    sys.exit(1)

def parse_cmdline():
    parser = argparse.ArgumentParser(description='Auto Upgrade Packages')
    parser.add_argument("package", help="package to be upgraded")
    parser.add_argument("-t", "--to_version",
                            help="version to upgrade the package to")
    parser.add_argument("-m", "--send_mail",
                            help="send mail when finished", action="store_true")
    return parser.parse_args()

def get_poky_dir():
    return os.path.realpath(os.path.dirname(__file__) + "/..")

def get_build_dir():
    return os.getenv('BUILDDIR')

def bb_exec_cmd(recipe, cmd=None, options=None):
    os.chdir(get_build_dir())
    bb_cmd = ["bitbake"]
    if cmd:
        bb_cmd.append("-c " + cmd)
    if options:
        bb_cmd.append(options)
    bb_cmd.append(recipe)
    if sp.call(bb_cmd, stdout=open(os.devnull, 'wb')) != 0:
        return True

    return False

def bb_fetch(recipe):
    global bb_env

    print("* Fetching %s ..." % recipe)
    if bb_exec_cmd(recipe, "fetch"):
        log.error(" Fetching %s failed! Check log for details: %s" % (recipe, os.path.realpath(bb_env["T"] + "/log.do_fetch")))
        return True

    return False

def bb_compile(recipe):
    print("* Compiling %s ..." % recipe)
    if bb_exec_cmd(recipe, "compile"):
        log.error(" Compilation %s failed!" % recipe)
        return True

    return False

def bb_get_failed_task(recipe):
    global bb_env
    with open(os.path.realpath(bb_env["T"] + "/log.task_order")) as task_order:
        last_line = list(task_order)[-1]
        m=re.match("^(.*) \(.*\): (.*)$", last_line)
        if m:
            return (m.group(1), m.group(2))

    return (None, None)

def bb(recipe):
    global bb_env

    print("* Compiling %s ..." % recipe)
    if bb_exec_cmd(recipe):
        (task, log_file) = bb_get_failed_task(recipe)
        log.error(" Task %s failed! Check log for details: %s" % (task, os.path.realpath(bb_env["T"] + "/" + log_file)))
        return True

    return False

def get_next_version(pkg):
    print("* Checking next available version...")
    os.chdir(get_build_dir())
    if sp.call(["bitbake", "-c checkpkg", pkg], stdout=open(os.devnull, 'wb')) != 0:
        log.error(" unable to check next version")
        return None

    csv = open(get_build_dir() + "/tmp/log/checkpkg.csv", "r")
    new_ver = csv.readlines()[1].split()[2]
    csv.close()

    return new_ver

def get_bb_env(pkg):
    print("* Fetching package environment...")
    try:
        bb_output = sp.check_output(["bitbake", "-e", pkg])
        assignment = re.compile("^([^ \t]*)=(.*)")
        bb_env = dict()
        for line in bb_output.split('\n'):
            m = assignment.match(line)
            if m:
                if m.group(1) in bb_env:
                    continue

                bb_env[m.group(1)] = m.group(2).strip("\"")

    except sp.CalledProcessError:
        log.error(" \'bitbake -e %s' failed, check the logs", pkg)
        return None

    return bb_env

def git(operation, repodir):
    p = sp.Popen(operation, cwd=repodir)
    returncode = p.wait()
    print("returncode = %d" % returncode)

def upgrade(pkg, ver=None):
    global bb_env

    if ver:
        print("Upgrade package '%s' to version %s\n" % (pkg, ver))
    else:
        print("Upgrade package '%s' to next available version\n" % pkg)

        ver = get_next_version(pkg)

    bb_env = get_bb_env(pkg)

    if ver == bb_env['PKGV']:
        print("* No need to upgrade: next version and current version coincide: %s!" % ver)
        return
    else:
        print("* Upgrade %s from %s to %s" % (pkg, bb_env['PKGV'], ver))

    # fetch the original package
    if bb_fetch(pkg):
        log.error(" Failed to fetch the original %s package. Check your network settings!" % pkg)
        return True

    # move the recipe to the next version

    return


if __name__ == "__main__":
    if not os.getenv('BUILDDIR', False):
        print("You must source oe-init-build-env before running this script!\n")
        exit(1)

    log.basicConfig(format='%(levelname)s:%(message)s', level=log.DEBUG)

    args = parse_cmdline()

    bb_env = get_bb_env(args.package)
#[lp]    upgrade(args.package, args.to_version)
    git(["git", "status"], get_poky_dir())

