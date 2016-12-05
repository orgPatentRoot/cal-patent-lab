#!/usr/bin/env python
import HTMLParser
import MySQLdb
from os import listdir
from os.path import isfile, join
import parsers.parser_new_data as parser
import re
import signal
import sys

MYSQL_HOST = "cal-patent-lab.chhaitskv8dz.us-west-2.rds.amazonaws.com"
MYSQL_USERNAME = ***REMOVED***
MYSQL_PASSWORD = ***REMOVED***
MYSQL_DB = ***REMOVED***
MYSQL_TABLE = "patents_decision"
db_conn = None

def db(host, username, psswd, database_name):
    return MySQLdb.connect(host, username, psswd, database_name)


def insert_decision(cursor, table_name, insert_queue):
    # SQL query to add a new row containing patent id and deciscion
    # the invalidated field of the table takes an int as an input
    query_placeholder = ",".join(["(%s, %s, %s)"] * len(insert_queue))
    sql_query = ('INSERT INTO {} (patent_id, invalidated, claims_text) VALUES '
       '{}'.format(table_name, query_placeholder))
    data_values = []
    for patent_id, decision, claim_text in insert_queue:
        data_values.append(patent_id)
        data_values.append(decision)
        data_values.append(claim_text)
    cursor.execute(sql_query, data_values)
    return


def update_table(db):
    # status contains false if the db was not updated
    status = False
    try:
        db.commit()
        status = True
    except:
        db.rollback()
    return status


def handle_ctrl_c(signal, frame):
    print("SIGINT caught, committing to DB and shutting down")
    status = update_table(db_conn)
    if not status:
        print("WARNING: error committing to DB")
    sys.exit(0)


def main():
    argv = sys.argv[1:]
    if len(argv) not in (2, 3):
        print("Usage: populate_db2.py path_to_parsed_files path_to_patent_tsv_files [path_to_save_file]")
        sys.exit(1)
    
    # Catch SIGINT (e.g. generated by Ctrl-C on the keyboard)
    signal.signal(signal.SIGINT, handle_ctrl_c)
    
    srcdir = argv[0]
    tsvdir = argv[1]
    savefile = None
    done = []
    if len(argv) == 3:
        savefile = argv[2]
    ptabfiles = [join(srcdir, f) for f in listdir(srcdir) if isfile(join(srcdir, f))]
    tsvfiles = [join(tsvdir, f) for f in listdir(tsvdir) if isfile(join(tsvdir, f))]
    if savefile and isfile(savefile):
        with open(savefile) as fd:
            done = [line.strip() for line in fd.readlines() if len(line.strip()) > 0]
    tsvfiles = list(set(tsvfiles) - set(done))
    
    global db_conn
    db_conn = db(MYSQL_HOST, MYSQL_USERNAME, MYSQL_PASSWORD, MYSQL_DB)
    cursor = db_conn.cursor()
    h = HTMLParser.HTMLParser()
    
    # Get list of all patent IDs in DB
    print("Getting all existing patent IDs in DB")
    cursor.execute("select patent_id from patents_decision")
    existing_IDs = set([elem[0] for elem in cursor.fetchall()])
    
    # Build decision table from parsed PTAB results
    decision_table = dict()
    for file_name in ptabfiles:
        patent_id = parser.parsePatentId(file_name)
        if patent_id:
            decision = (parser.parseDecision(file_name) == "invalidated")
            # If this patent was invalidated at any point, keep it invalidated
            if patent_id in decision_table:
                decision = decision or decision_table[patent_id][0]
            # Has this patent been written to the DB earlier?
            is_written = patent_id in existing_IDs
            decision_table[patent_id] = [decision, is_written]
    
    if savefile:
        save_fd = open(savefile, "a")
    
    print("Reading patents from TSV files")
    for file_name in tsvfiles:
        print("Reading from {}".format(file_name))
        claims = open(file_name, 'r')
        insert_queue = []
        i = 0
        for line in claims:
            i += 1
            if i & 0x3ff == 0:
                print("Reading line {}".format(i))
            # isolate patent #, claims text
            patent_id, patent_body = line.split('\t')
            patent_id = patent_id.strip()
            # Some entries in the TSV files are published applications, e.g. 2004/0204653
            # Skip these, since we only want granted patents
            if patent_id in existing_IDs or "/" in patent_id:
                continue
            _, claim_text = patent_body.split('CLAIMS. ')
            # Strip out any invalid ASCII to avoid string decode issues
            claim_text = re.sub('[^\x00-\x7f]', '', claim_text) 
            claim_text = h.unescape(claim_text).encode("utf-8")
            dec = None
            if patent_id in decision_table:
                decision_table[patent_id][1] = True  # Mark patent with decision as written
                decision = decision_table[patent_id][0]
            insert_queue.append((patent_id, decision, claim_text))
            existing_IDs.add(patent_id)
            if len(insert_queue) == 128:
                print("Flushing batch")
                status = insert_decision(cursor, MYSQL_TABLE, insert_queue)
                insert_queue = []
        # Flush the insert queue
        if len(insert_queue) > 0:
            status = insert_decision(cursor, MYSQL_TABLE, insert_queue)
        # Commit transaction after every TSV file
        print("Committing to DB")
        status = update_table(db_conn)
        if not status:
            print("WARNING: error committing to DB")
        # Add TSV file to list of completed files
        if savefile:
            print("Writing {} to savefile".format(file_name))
            save_fd.write("{}\n".format(file_name))
            save_fd.flush()
    if savefile:
        save_fd.close()
    
    insert_queue = []
    for patent_id in decision_table:
        decision, is_written = decision_table[patent_id]
        if is_written or patent_id in existing_IDs:
            continue
        decision = decision_table[patent_id][0]
        insert_queue.append((patent_id, decision, claim_text))
        if len(insert_queue) == 128:
            print("Flushing batch")
            status = insert_decision(cursor, MYSQL_TABLE, insert_queue)
            insert_queue = []
    # Flush the insert queue
    if len(insert_queue) > 0:
        status = insert_decision(cursor, MYSQL_TABLE, insert_queue)
        insert_queue = []
        print(status)
    print("Committing to DB")
    status = update_table(db_conn)
    if not status:
        print("WARNING: error committing to DB")
    db_conn.close()


if __name__ == "__main__":
    main()


