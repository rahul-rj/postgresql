#!/usr/bin/env python

# Starts up postgresql within the container.

from __future__ import print_function

import os
import stat
import pwd
import grp
import sys
import socket
import shutil
import subprocess
import psycopg2
import psycopg2.extras
import time
import jinja2
from jinja2 import exceptions
import logging


# setup logging
logger = logging.getLogger('org.postgresql')
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)


def get_password(USERNAME):
    """ Getting password either from secret file, environment variable or setting it to default
    """

    FILENAME = '/run/secrets/{}'.format(USERNAME)
    if os.path.isfile(FILENAME):
        with open(FILENAME) as f:
            DB_PASSWORD = f.read().splitlines()[0]
    else:
        DB_PASSWORD = 'postgres'
    return DB_PASSWORD


def alter_password_all():
    """ Updating all passwords in DB with the passwords from secret file
    """

    PATH_HBA='/opt/pgsql/data/pg_hba.conf'
    shutil.copy(PATH_HBA,'{}_orig'.format(PATH_HBA))
    shutil.copy('/opt/pgsql_templates/pg_hba.conf_internal', PATH_HBA)
    subprocess.call('su postgres -c "/usr/bin/pg_ctl start -w -D {}"'.format(PATH), shell=True)
    subprocess.call('/usr/bin/psql -U postgres -c \"alter user {} with encrypted password \'{}\'\"'.format('postgres', get_password('postgres')), shell=True)
    subprocess.call('su postgres -c "/usr/bin/pg_ctl stop -w -D {}"'.format(PATH), shell=True)
    os.rename('{}_orig'.format(PATH_HBA), PATH_HBA)
    os.chown(PATH_HBA, UID, GID)
    logger.info('Users password updated from secrets.')


def set_env():
    """ Setting ENV for the run script
    """

    global HA
    global UID
    global GID
    global DB
    global PATH
    global PGDBVERSION
    global POSTGRESQL_HOST
    global POSTGRESQL_CLIENT_PORT
    global MAX_CONNECTIONS
    global EFFECTIVE_CACHE_SIZE
    global ARCHIVE_TIMEOUT
    global HOSTNAME
    global SERVICE_NAME

    PGDBVERSION = '/opt/pgsql/data/PG_VERSION'
    PATH = '/opt/pgsql/data'
    UID = pwd.getpwnam('postgres').pw_uid
    GID = grp.getgrnam('postgres').gr_gid
    ARCHIVE_TIMEOUT = os.environ.get('ARCHIVE_TIMEOUT', '600')
    HA = os.environ.get('HA', 'DISABLE').upper()
    DB = os.environ.get('DB', 'MASTER').upper()
    HOSTNAME = socket.gethostname().split('.')
    POSTGRESQL_CLIENT_PORT = os.environ.get('POSTGRESQL_CLIENT_PORT', '5432')
    MAX_CONNECTIONS = os.environ.get('MAX_CONNECTIONS', 400)
    EFFECTIVE_CACHE_SIZE = os.environ.get('EFFECTIVE_CACHE_SIZE', '128MB')
    SERVICE_NAME = os.environ.get('SERVICE_NAME', HOSTNAME)
    if 'master' in SERVICE_NAME or 'MASTER' in SERVICE_NAME:
        DB = 'MASTER'
        HA = 'ENABLE'
        POSTGRESQL_HOST = SERVICE_NAME.replace('master', 'slave')
    elif 'slave' in SERVICE_NAME or 'SLAVE' in SERVICE_NAME:
        DB = 'SLAVE'
        HA = 'ENABLE'
        POSTGRESQL_HOST = SERVICE_NAME.replace('slave', 'master')
    else:
        POSTGRESQL_HOST = SERVICE_NAME


def render(name, conf):
  """ Render configuration file from template.
  """

  template_filename = '/opt/pgsql_templates/{}'.format(name)
  output_filename = '{}/{}'.format(PATH, name)

  env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.dirname(template_filename)),
                           extensions=['jinja2.ext.with_'], trim_blocks=True)

  try:
    template = env.get_template(os.path.basename(template_filename))
  except exceptions.TemplateNotFound as e:
        logger.info('Error reading template file {}!'.format(template_filename))

  logger.debug(conf)
  with open(output_filename, "w") as f:
        f.write(template.render(conf))


def configure_timezone(conf):

  logger.info('Configuring timezone for postgresql.')
  TIMEZONE = os.environ.get('TIMEZONE', 'US/Eastern')
  conf['TIMEZONE'] = TIMEZONE


def initialize_DB():

  logger.info('Initializing postgresql DB.')

  os.chown(PATH, UID, GID)
  os.chmod(PATH, stat.S_IRWXU)

  # initialize DB
  subprocess.call('su postgres -c "/usr/bin/pg_ctl init -D {}"'.format(PATH), shell=True)
  os.makedirs('{}/archive'.format(PATH))
  os.chown('{}/archive'.format(PATH), UID, GID)


def load_DB():

  logger.info('Creating initial DB in postgres.')

  conf = {}
  conf['POSTGRES'] = get_password('postgres')
  render('pgschema.sql', conf)

  # setup users and initial databases
  os.setuid(UID)
  subprocess.call('/usr/bin/pg_ctl start -w -D {}'.format(PATH), shell=True)
  subprocess.call('/usr/bin/psql -U postgres -f {}/pgschema.sql'.format(PATH), shell=True)
  logger.info('PostgreSQL DB schema created'.format)
  subprocess.call('/usr/bin/pg_ctl stop -w -D {}'.format(PATH), shell=True)


def configure_postgres():

  conf = {}
  conf['HA'] = HA
  conf['ARCHIVE_TIMEOUT'] = ARCHIVE_TIMEOUT
  conf['MAX_CONNECTIONS'] = MAX_CONNECTIONS
  conf['EFFECTIVE_CACHE_SIZE'] = EFFECTIVE_CACHE_SIZE
  configure_timezone(conf)

  render('postgresql.conf', conf)


def configure_pg_hba():

  conf = {}
  if HA == 'ENABLE':
      configure_trigger()
      POSTGRESQL_HOST = '10.0.0.0'
      conf['HA'] = HA
      conf['POSTGRESQL_HOST'] = POSTGRESQL_HOST

  render('pg_hba.conf', conf)

def configure_trigger():

  conf = {}
  conf['SERVICE_NAME'] = SERVICE_NAME

  render('trigger.py', conf)

def sync_data():

  logger.info('Syncing data from Master POSTGRES.')
  shutil.rmtree(PATH, ignore_errors=True)
  if ( POSTGRESQL_HOST and POSTGRESQL_CLIENT_PORT ):
     subprocess.Popen(["/usr/bin/pg_basebackup", "-v", "-D", PATH, "-R", "-P", "-h", POSTGRESQL_HOST, "-p", POSTGRESQL_CLIENT_PORT, "-U", "repuser"], stdout=subprocess.PIPE).communicate()[0]
     PATH_STATUS='/opt/pgsql/data/pg_xlog/archive_status'
     if not os.path.exists(PATH_STATUS):
         os.makedirs(PATH_STATUS)
     os.chown(PATH, UID, GID)
     for root, dirs, files in os.walk(PATH):
       for momo in dirs:
         os.chown(os.path.join(root, momo), UID, GID)
       for momo in files:
         os.chown(os.path.join(root, momo), UID, GID)
     os.chmod(PATH, stat.S_IRWXU)


def configure_recovery():

  conf = {}

  conf['POSTGRESQL_HOST'] = POSTGRESQL_HOST
  conf['POSTGRESQL_CLIENT_PORT'] = POSTGRESQL_CLIENT_PORT
  render('recovery.conf', conf)


def check_status_peer(MAX_TRY):
  logger.info('*** Wait for Peer DB to come up ***')
  start = time.time()
  end_by = start + MAX_TRY
  con = None
  POSTGRESQL_COMMAND="select pg_is_in_recovery()"
  while time.time() < end_by:
    try:
      con = psycopg2.connect(host=POSTGRESQL_HOST, port=POSTGRESQL_CLIENT_PORT,
                             database='postgres', user='postgres', password=get_password('postgres'))
      cursor = con.cursor(cursor_factory=psycopg2.extras.DictCursor)
      cursor.execute(POSTGRESQL_COMMAND)
      answer = cursor.fetchall()
      cursor.close()
      con.close()
      logger.info('*** Peer DB is up ***')
      if not answer[0][0]:
          logger.info('Starting this server as Slave.')
          DBRECOVERY='/opt/pgsql/data/recovery.conf'
          if not os.path.exists(DBRECOVERY):
              sync_data()
              configure_recovery()
              configure_pg_hba()
      break
    except Exception as e:
      logger.info('*** Peer DB is not up yet, due to exception: {}'.format(e.message))
      if not os.path.exists(PGDBVERSION):
          if DB=='MASTER':
              initialize_DB()
              load_DB()
              configure_postgres()
              configure_pg_hba()
              break
      pass
    finally:
      if con:
        con.close()
    time.sleep(1)

def run():

  logger.info('Starting POSTGRES.')

  if HA=='ENABLE':
      subprocess.Popen('/usr/bin/python /opt/pgsql/data/trigger.py', shell=True)
  # Getting postgres user id from OS
  os.setuid(UID)
  os.execl('/usr/bin/postgres', 'postgres', '-D', PATH)


def main(argv):

    set_env()
    MAX_TRY = os.environ.get('MAX_TRY', 10)
    #check if DB is already installed
    if not os.path.exists(PGDBVERSION):
        if HA=='ENABLE':
            check_status_peer(MAX_TRY)
            run()
        else:
            initialize_DB()
            load_DB()
            configure_postgres()
            configure_pg_hba()
            run()
    else:
        if HA=='ENABLE':
            check_status_peer(MAX_TRY)
        alter_password_all()
        run()


if __name__ == "__main__":
    main(sys.argv)
