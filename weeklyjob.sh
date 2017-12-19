#!/bin/bash

# Cronjob which can be run weekly to run the upgrade helper script.
# Add the job in /etc/crontab like below.
#
# It will execute weekly at the same hour (8 AM).
#
# 00 8   * * 6   auh  /home/auh/bin/weeklyjob.sh

# Re-assign these to match your setup!
auh_dir=~/auto-upgrade-helper
poky_dir=~/poky
build_dir=~/build-tmp-auh-upgrades
sstate_dir=~/sstate-cache

pushd $poky_dir

# Base the upgrades on poky master
git fetch origin
git checkout -B tmp-auh-upgrades origin/master

source $poky_dir/oe-init-build-env $build_dir
$auh_dir/upgradehelper.py all

# clean up to avoid the disk filling up
rm -rf $build_dir/tmp/
find $sstate_dir -atime +10 -delete

popd
