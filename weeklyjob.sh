#!/bin/bash

# Cronjob which can be run weekly to run the upgrade helper script.
# Add the job in /etc/crontab like below.
#
# It will execute weekly at the same hour (8 AM).
#
# 00 8   * * 6   marius  /home/marius/work/yocto_package_upgrade_helper-main/weeklyjob.sh

BUILDIR=/media/SSD/build3/
POKYSOURCE=/home/marius/work/poky_http/poky/oe-init-build-env
UPGRADESCRIPT=/home/marius/work/yocto_package_upgrade_helper-main/upgradehelper.py
LOGFILE=/home/marius/work/yocto_package_upgrade_helper-main/upgradehelper.log

cd $BUILDIR
source $POKYSOURCE $BUILDIR
python $UPGRADESCRIPT all &> $LOGFILE
