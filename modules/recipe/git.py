#!/usr/bin/env python
# vim: set ts=4 sw=4 et:
#
# Copyright (c) 2013 - 2014 Intel Corporation
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
# AUTHORS
# Laurentiu Palcu   <laurentiu.palcu@intel.com>
# Marius Avram      <marius.avram@intel.com>
#

from recipe.base import Recipe

class GitRecipe(Recipe):
    def _extract_tag_from_ver(self, ver):
        m = re.match("(.*)\+.*\+.*", ver)
        if m is not None:
            return m.group(1)

        # allow errors in the reporting system
        return ver

    def _get_tag_sha1(self, new_tag):
        m = re.match(".*(git://[^ ;]*).*", self.env['SRC_URI'])
        if m is None:
            raise Error("could not extract repo url from SRC_URI")

        repo_url = m.group(1)
        tags = self.git.ls_remote(repo_url, "--tags")

        # Try to find tag ending with ^{}
        for tag in tags.split('\n'):
            if tag.endswith(new_tag + "^{}"):
                return tag.split()[0]

        # If not found, try to find simple tag
        for tag in tags.split('\n'):
            if tag.endswith(new_tag):
                return tag.split()[0]

        return None

    def rename(self):
        old_git_tag = self._extract_tag_from_ver(self.env['PKGV'])
        new_git_tag = self._extract_tag_from_ver(self.new_ver)

        if new_git_tag == old_git_tag:
            raise UpgradeNotNeededError()

        tag_sha1 = self._get_tag_sha1(new_git_tag)
        if tag_sha1 is None:
            raise Error("could not extract tag sha1")

        for f in os.listdir(self.recipe_dir):
            full_path_f = os.path.join(self.recipe_dir, f)
            if os.path.isfile(full_path_f) and \
                    ((f.find(self.env['PN']) == 0 and (f.find(old_git_tag) != -1 or
                      f.find("git") != -1) and f.find(".bb") != -1) or
                     (f.find(self.env['PN']) == 0 and f.find(".inc") != -1)):
                with open(full_path_f + ".tmp", "w+") as temp_recipe:
                    with open(full_path_f) as recipe:
                        for line in recipe:
                            m1 = re.match("^SRCREV *= *\".*\"", line)
                            m2 = re.match("PV *= *\"[^\+]*(.*)\"", line)
                            if m1 is not None:
                                temp_recipe.write("SRCREV = \"" + tag_sha1 + "\"\n")
                            elif m2 is not None:
                                temp_recipe.write("PV = \"" + new_git_tag + m2.group(1) + "\"\n")
                            else:
                                temp_recipe.write(line)

                os.rename(full_path_f + ".tmp", full_path_f)

        self.env['PKGV'] = old_git_tag
        self.new_ver = new_git_tag

        super(GitRecipe, self).rename()

    def fetch(self):
        pass

