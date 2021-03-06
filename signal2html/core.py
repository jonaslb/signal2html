# -*- coding: utf-8 -*-

"""Core functionality

Author: Gertjan van den Burg

"""

import os
import warnings
import sqlite3

from .exceptions import (
    DatabaseNotFound,
    DatabaseVersionNotFound,
)
from .models import (
    Attachment,
    MMSMessageRecord,
    Quote,
    Recipient,
    RecipientId,
    SMSMessageRecord,
    Thread,
)
from .html import dump_thread


def check_backup(backup_dir):
    """Check that we have the necessary files"""
    if not os.path.join(backup_dir, "database.sqlite"):
        raise DatabaseNotFound
    if not os.path.join(backup_dir, "DatabaseVersion.sbf"):
        raise DatabaseVersionNotFound
    with open(os.path.join(backup_dir, "DatabaseVersion.sbf"), "r") as fp:
        version_str = fp.read()

    # We have only ever seen database version 23, so we don't proceed if it's
    # not that. Testing and pull requests welcome.
    # However, this is the ghetto-adaptation for version 65 / jonaslb
    version = version_str.split(":")[-1].strip()
    if not version == "65":
        warnings.warn(
            f"Warning: Found untested Signal database version: {version}."
        )


def make_recipient(db, recipient_id):
    """ Create a Recipient instance from a given recipient id """
    qry = db.execute(
        "SELECT group_id, system_display_name, profile_joined_name, color from recipient where _id=?", (recipient_id,)
    )
    groupid, name, joined_name, color = qry.fetchone()

    if color is None:
        from .html_colors import COLORMAP
        from random import choice as rchoice
        color = rchoice(list(COLORMAP.keys()))

    isgroup = groupid is not None

    if isgroup:
        qry = db.execute(
            "SELECT title FROM groups WHERE group_id=?", (groupid,)
        )
        name = qry.fetchone()[0]

    if name is None:
        if joined_name is None:
            raise ValueError(f"Could not find a name for recipient id {recipient_id}")
        name = joined_name

    rid = RecipientId(recipient_id)
    return Recipient(rid, name=name, color=color, isgroup=isgroup)


def get_sms_records(db, thread):
    """ Collect all the SMS records for a given thread """
    sms_records = []
    sms_qry = db.execute(
        "SELECT _id, address, date, date_sent, body, type "
        "FROM sms WHERE thread_id=?",
        (thread._id,),
    )
    qry_res = sms_qry.fetchall()
    for _id, address, date, date_sent, body, _type in qry_res:
        sms = SMSMessageRecord(
            _id=_id,
            addressRecipient=make_recipient(db, address),
            recipient=thread.recipient,
            dateSent=date_sent,
            dateReceived=date,
            threadId=thread._id,
            body=body,
            _type=_type,
        )
        sms_records.append(sms)
    return sms_records


def get_attachment_filename(_id, unique_id, backup_dir):
    """ Get the absolute path of an attachment, warn if it doesn't exist"""
    fname = f"Attachment_{_id}_{unique_id}.bin"
    pth = os.path.abspath(os.path.join(backup_dir, fname))
    if not os.path.exists(pth):
        warnings.warn(f"Warning: couldn't find attachment {pth}!")
        return None
    return pth


def add_mms_attachments(db, mms, backup_dir):
    """ Add all attachment objects to MMS message """
    qry = db.execute(
        "SELECT _id, ct, unique_id, voice_note, width, height, quote "
        "FROM part WHERE mid=?",
        (mms._id,),
    )
    for _id, ct, unique_id, voice_note, width, height, quote in qry:
        a = Attachment(
            contentType=ct,
            unique_id=unique_id,
            fileName=get_attachment_filename(_id, unique_id, backup_dir),
            voiceNote=voice_note,
            width=width,
            height=height,
            quote=quote,
        )
        mms.attachments.append(a)


def get_mms_records(db, thread, recipients, backup_dir):
    """ Collect all MMS records for a given thread """
    mms_records = []
    qry = db.execute(
        "SELECT _id, address, date, date_received, body, quote_id, "
        "quote_author, quote_body, msg_box FROM mms WHERE thread_id=?",
        (thread._id,),
    )
    qry_res = qry.fetchall()
    for (
        _id,
        address,
        date,
        date_received,
        body,
        quote_id,
        quote_author,
        quote_body,
        msg_box,
    ) in qry_res:
        quote = None
        if quote_id:
            quote_auth = next(
                (r for r in recipients if str(r.recipientId._id) == str(quote_author)),
                None,
            )
            if not quote_auth:
                raise ValueError("Unknown quote author: %s" % quote_author)
            quote = Quote(_id=quote_id, author=quote_auth, text=quote_body)

        mms = MMSMessageRecord(
            _id=_id,
            addressRecipient=make_recipient(db, address),
            recipient=thread.recipient,
            dateSent=date,
            dateReceived=date_received,
            threadId=thread._id,
            body=body,
            quote=quote,
            attachments=[],
            _type=msg_box,
        )
        mms_records.append(mms)

    for mms in mms_records:
        add_mms_attachments(db, mms, backup_dir)

    return mms_records


def populate_thread(db, thread, recipients, backup_dir):
    """ Populate a thread with all corresponding messages """
    sms_records = get_sms_records(db, thread)
    mms_records = get_mms_records(db, thread, recipients, backup_dir)
    thread.sms = sms_records
    thread.mms = mms_records


def process_backup(backup_dir, output_dir):
    """ Main functionality to convert database into HTML """

    # Verify backup and open database
    check_backup(backup_dir)
    db_file = os.path.join(backup_dir, "database.sqlite")
    db_conn = sqlite3.connect(db_file)
    db = db_conn.cursor()

    # Start by getting the Threads from the database
    query = db.execute("SELECT _id, recipient_ids FROM thread")
    threads = query.fetchall()

    # Now turn the recipients from the threads into Recipient classes
    recipients = []
    for _, recipient_id in threads:
        r = make_recipient(db, recipient_id)
        recipients.append(r)

    # Combine the recipient objects and the thread info into Thread objects
    for (_id, _), recipient in zip(threads, recipients):
        t = Thread(_id=_id, recipient=recipient)
        populate_thread(db, t, recipients, backup_dir)
        dump_thread(t, output_dir)

    db.close()
