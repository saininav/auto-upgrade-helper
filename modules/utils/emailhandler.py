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

import os
import logging as log
from logging import error as E
from logging import info as I
from smtplib import SMTP
import mimetypes
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.generator import Generator
import shutil
from io import StringIO

class Email(object):
    def __init__(self, settings):
        self.smtp_host = None
        self.smtp_port = None
        self.from_addr = None
        if "smtp" in settings:
            smtp_entry = settings["smtp"].split(":")
            if len(smtp_entry) == 1:
                self.smtp_host = smtp_entry[0]
                self.smtp_port = 25
            elif len(smtp_entry) == 2:
                self.smtp_host = smtp_entry[0]
                self.smtp_port = smtp_entry[1]
        else:
            E(" smtp host not set! Sending emails disabled!")

        if "from" in settings:
            self.from_addr = settings["from"]
        else:
            E(" 'From' address not set! Sending emails disabled!")

        super(Email, self).__init__()

    def send_email(self, to_addr, subject, text, files=[], cc_addr=None):
        if self.smtp_host is None or self.from_addr is None:
            return 0

        I(" Sending email to: %s" % to_addr)

        msg = MIMEMultipart()
        msg['From'] = self.from_addr
        if type(to_addr) is list:
            msg['To'] = ', '.join(to_addr)
        else:
            msg['To'] = to_addr
        if cc_addr is not None:
            if type(cc_addr) is list:
                msg['Cc'] = ', '.join(cc_addr)
            else:
                msg['Cc'] = cc_addr
        msg['Subject'] = subject

        msg.attach(MIMEText(text))

        for file in files:
            ctype, encoding = mimetypes.guess_type(file)
            if ctype is None or encoding is not None:
                ctype = 'application/octet-stream'
            maintype, subtype = ctype.split('/', 1)

            if maintype == "text":
                attachment = MIMEText(open(file).read(), _subtype=subtype)
            else:
                attachment = MIMEBase(maintype, _subtype=subtype)
                attachment.set_payload(open(file, 'rb').read())

            attachment.add_header('Content-Disposition', 'attachment; filename="%s"'
                                  % os.path.basename(file))
            msg.attach(attachment)

        out = StringIO()
        Generator(out, mangle_from_=False).flatten(msg)
        msg_text = out.getvalue()

        try:
            smtp = SMTP(self.smtp_host, self.smtp_port)
            smtp.sendmail(self.from_addr, to_addr, msg_text)
            smtp.close()
        except Exception as e:
            E("Could not send email: %s" % str(e))

