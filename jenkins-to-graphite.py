#
# Send various statistics about jenkins to graphite
#
# Jeremy Katz <katzj@hubspot.com>
# Copyright 2012, HubSpot, Inc.
#
# Updates by Aleksey Maksimov <ctpeko3a@gmail.com>:
#
# Strict PEP8
# Added Requests
# Removed json dependency
# Added Docopt
#
# Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
"""
Send various statistics about jenkins to graphite

Usage:
    jenkins-to-graphite --jenkins-url URL --graphite-server HOST
        [--graphite-port PORT] [--jenkins-user USER --jenkins-password PASS]
        [--jobs VIEW] [--prefix PREFIX]
        [--label LABEL] [--loglevel LEVEL]

Options:
    -h --help                   Show this screen
    --jenkins-url URL           Base url of your jenkins server
                                (ex. http://jenkins.example.com)
    --graphite-server HOST      Host name of the server running graphite
    --graphite-port PORT        Graphite port. [default: 2003]
    --jenkins-user USER         User to authenticate with for jenkins
    --jenkins-password PASS     Password or API key for authenticating
                                with jenkins
    --jobs VIEW                 Jobs view to monitor for success/failure
    --prefix PREFIX             Graphite metric prefix.
                                [default: jenkins]
    --label LABEL               Fetch stats applicable to this node label.
                                Can bee applied multiple times for
                                monitoring more labels.
    --loglevel LEVEL            Logging level [default: INFO]
"""

import ast
from docopt import docopt
import logging
import requests
import socket
import time

log = logging.getLogger(__name__)


class JenkinsServer(object):
    def __init__(self, base_url, username=None, password=None):
        self.base_url = base_url
        self._request_args = {'auth': (username, password)} \
            if username else {}

    def get_raw_data(self, url):
        """Get the data from jenkins at @url and return it as a dictionary"""

        full_url = "%s/%s" % (self.base_url, url)
        log.debug('Fetching from %s', full_url)
        response = requests.get(full_url, **self._request_args)
        log.debug('HTTP status code: %s', response.status_code)
        if response.status_code != 200:
            log.error('Unable to read from %s: %s',
                      full_url, response.status_code)
            return {}
        else:
            return ast.literal_eval(response.text)

    def get_data(self, url):
        return self.get_raw_data("%s/api/python" % url)

    @property
    def build_info_min(self):
        url = ('view/All/timeline/data?min=%d&max=%d'
               % ((time.time() - 60) * 1000, time.time() * 1000))
        return self.get_raw_data(url)

    @property
    def build_info_hour(self):
        url = ('view/All/timeline/data?min=%d&max=%d'
               % ((time.time() - 3600) * 1000, time.time() * 1000))
        return self.get_raw_data(url)


class GraphiteServer(object):
    def __init__(self, server, port, prefix):
        self.server = server
        self.port = int(port)
        self.prefix = prefix.rstrip('.')

        self.data = {}

    def add_data(self, key, value):
        self.data["%s.%s" % (self.prefix, key)] = value

    def _data_as_msg(self):
        msg = ""
        now = time.time()
        for (key, val) in self.data.items():
            msg += "%s %s %s\n" % (key, val, now)
        return msg

    def send(self):
        try:
            formatted_data = self._data_as_msg()
            log.debug('Sending to graphite: %s', formatted_data)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((self.server, self.port))
            s.sendall(formatted_data)
            s.close()
            log.debug('Data successfully send to graphite')
        except Exception, e:
            log.error("Unable to send msg to graphite: %s", str(e))
            return False

        return True


def gather_and_send_stats(opts, jenkins, graphite):
    # Gather stats
    executor_info = jenkins.get_data('computer')
    queue_info = jenkins.get_data('queue')
    build_info_min = jenkins.build_info_min
    build_info_hour = jenkins.build_info_hour

    graphite.add_data('queue.size', len(queue_info.get('items', [])))

    graphite.add_data('builds.started_builds_last_minute',
                      len(build_info_min.get('events', [])))
    graphite.add_data('builds.started_builds_last_hour',
                      len(build_info_hour.get('events', [])))

    graphite.add_data('executors.total',
                      executor_info.get('totalExecutors', 0))
    graphite.add_data('executors.busy',
                      executor_info.get('busyExecutors', 0))
    graphite.add_data('executors.free',
                      executor_info.get('totalExecutors', 0) -
                      executor_info.get('busyExecutors', 0))

    nodes_total = executor_info.get('computer', [])
    nodes_offline = [j for j in nodes_total if j.get('offline')]
    graphite.add_data('nodes.total', len(nodes_total))
    graphite.add_data('nodes.offline', len(nodes_offline))
    graphite.add_data('nodes.online', len(nodes_total) - len(nodes_offline))

    if opts['--labels']:
        for label in opts['--labels']:
            label_info = jenkins.get_data('label/%s' % label)
            graphite.add_data('labels.%s.jobs.tiedJobs'
                              % label, len(label_info.get('tiedJobs', [])))
            graphite.add_data('labels.%s.nodes.total'
                              % label, len(label_info.get('nodes', [])))
            graphite.add_data('labels.%s.executors.total'
                              % label, label_info.get('totalExecutors', 0))
            graphite.add_data('labels.%s.executors.busy'
                              % label, label_info.get('busyExecutors', 0))
            graphite.add_data('labels.%s.executors.free' % label,
                              label_info.get('totalExecutors', 0) -
                              label_info.get('busyExecutors', 0))

    if opts['--jobs']:
        builds_info = jenkins.get_data('/view/%s' % opts['--jobs'])
        jobs = builds_info.get('jobs', [])
        ok = [j for j in jobs if j.get('color', 0) == 'blue']
        fail = [j for j in jobs if j.get('color', 0) == 'red']
        warn = [j for j in jobs if j.get('color', 0) == 'yellow']
        graphite.add_data('jobs.total', len(jobs))
        graphite.add_data('jobs.ok', len(ok))
        graphite.add_data('jobs.fail', len(fail))
        graphite.add_data('jobs.warn', len(warn))

    graphite.send()


def main():
    opts = docopt(__doc__)
    # Setup logging
    loglevel = opts['--loglevel'].upper()
    fmt = '%(asctime)s %(levelname)s: %(message)s'
    if loglevel == 'DEBUG':
        fmt = '%(asctime)s %(name)s@%(lineno)d %(levelname)s: %(message)s'

    logging.basicConfig(format=fmt, level=loglevel)

    # Init servers
    jenkins = JenkinsServer(opts['--jenkins_url'], opts['--jenkins_user'],
                            opts['--jenkins_password'])
    graphite = GraphiteServer(opts['--graphite_server'],
                              opts['--graphite_port'],
                              opts['--prefix'])

    gather_and_send_stats(opts, jenkins, graphite)

if __name__ == '__main__':
    main()
