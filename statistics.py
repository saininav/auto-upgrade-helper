#!/usr/bin/env python
# vim: set ts=4 sw=4 et:
#
# Copyright (c) 2013 - 2014 Intel Corporation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
#
# AUTHORS
# Laurentiu Palcu   <laurentiu.palcu@intel.com>
# Marius Avram      <marius.avram@intel.com>
#

class Statistics(object):
    def __init__(self):
        self.succeeded = dict()
        self.failed = dict()
        self.succeeded["total"] = 0
        self.failed["total"] = 0
        self.upgrade_stats = dict()
        self.maintainers = set()
        self.total_attempted = 0

    def update(self, pn, new_ver, maintainer, error):
        if type(error).__name__ == "UpgradeNotNeededError":
            return
        elif error is None:
            status = "Succeeded"
        else:
            status = str(error)

        if not status in self.upgrade_stats:
            self.upgrade_stats[status] = []

        self.upgrade_stats[status].append((pn, new_ver, maintainer))

        # add maintainer to the set of unique maintainers
        self.maintainers.add(maintainer)

        if not maintainer in self.succeeded:
            self.succeeded[maintainer] = 0
        if not maintainer in self.failed:
            self.failed[maintainer] = 0

        if status == "Succeeded":
            self.succeeded["total"] += 1
            self.succeeded[maintainer] += 1
        else:
            self.failed["total"] += 1
            self.failed[maintainer] += 1

        self.total_attempted += 1

    def pkg_stats(self):
        stat_msg = "\nUpgrade statistics:\n"
        stat_msg += "====================================================\n"
        for status in self.upgrade_stats:
            list_len = len(self.upgrade_stats[status])
            if list_len > 0:
                stat_msg += "* " + status + ": " + str(list_len) + "\n"

                for pkg, new_ver, maintainer in self.upgrade_stats[status]:
                    stat_msg += "    " + pkg + ", " + new_ver + ", " + \
                                maintainer + "\n"

        if self.total_attempted == 0:
            percent_succeded = 0
            percent_failed = 0
        else:
            percent_succeded = self.succeeded["total"] * 100.0 / self.total_attempted
            percent_failed = self.failed["total"] * 100.0 / self.total_attempted
        stat_msg += "++++++++++++++++++++++++++++++++++++++++++++++++++++\n"
        stat_msg += "TOTAL: attempted=%d succeeded=%d(%.2f%%) failed=%d(%.2f%%)\n\n" % \
                    (self.total_attempted, self.succeeded["total"],
                    percent_succeded,
                    self.failed["total"],
                    percent_failed)

        return stat_msg

    def maintainer_stats(self):
        stat_msg = "* Statistics per maintainer:\n"
        for m in self.maintainers:
            attempted = self.succeeded[m] + self.failed[m]
            stat_msg += "    %s: attempted=%d succeeded=%d(%.2f%%) failed=%d(%.2f%%)\n\n" % \
                        (m.split("@")[0], attempted, self.succeeded[m],
                        self.succeeded[m] * 100.0 / attempted,
                        self.failed[m],
                        self.failed[m] * 100.0 / attempted)

        return stat_msg
