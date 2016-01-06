#!/bin/bash

# Cronjob which can be run weekly to run the upgrade helper script.
# Add the job in /etc/crontab like below.
#
# It will execute weekly at the same hour (8 AM).
#
# 00 8   * * 6   auh  /home/auh/bin/weeklyjob.sh

auh_dir=~/auto-upgrade-helper
poky_dir=~/poky
build_dir=~/build

source $poky_dir/oe-init-build-env $build_dir
$auh_dir/upgradehelper.py all

#/usr/bin/rsync --delete --password-file /home/auh/rsync.passwd --copy-unsafe-links -zaHS /home/auh/work/ auh@downloads.yoctoproject.org::auh/
