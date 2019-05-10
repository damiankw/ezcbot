import json
import logging
import time
import os
import uuid
import threading
import sys
import random
import sqlite3

import config
import user
from apis import ezcapechat
from pages import acc
from util import string_util
from rtmplib import rtmp

sys.path.insert(0, 'util')
from string_util import *

__version__ = '2.0.0'
log = logging.getLogger(__name__)
CONFIG = config


class EZCBOT:
    # EZCBOT(<room name>, <user name>, [<email address>], [<password>], [<proxy>])
    # creates the BOT instance and collates data for use locally
    def __init__(self, room, username, email=None, password=None, proxy=None):
        # default configuration for local variables
        self.room_name = u'' + room        # the room we live in
        self.email = email                 # the authorised email address
        self.password = password           # the password for email address
        self.proxy = proxy                 # our proxy account

        self.connection = None             # the connection socket
        self.is_connected = False          # whether or not we are connected

        self.users = user.Users()          # a collection of users in the room
        self.users.add_client(username)    # add ourself
        
        
        self.db = sqlite3.connect(config.DBFILE)
        self.query = self.db.cursor()
        self.check_database();
        
        
        self.autogreet = {}
        self._pub_n_key = None
        self._room_id = 0
        self._msg_counter = 1
        self._mimic_user = ""


    # check_database()
    # checks to see if the database exists, create if it doesnt
    def check_database(self):
        # check if we have the correct number of tables
        self.query.execute("SELECT COUNT(name) FROM sqlite_master WHERE name LIKE 'ezc_%'")
        rows = self.query.fetchone()[0]
        if (rows == 0):
            print "WARNING: No tables found, creating database ..."
            print "- creating autogreet."
            self.query.execute("CREATE TABLE ezc_autogreet (nick VARCHAR(200), value TEXT, when_updated DATETIME, who_updated VARCHAR(200))")
            print "- creating users."
            self.query.execute("CREATE TABLE ezc_users (user VARCHAR(200), level INT, created DATETIME, when_updated DATETIME, who_updated VARCHAR(200))")
            print "- creating stats."
            self.query.execute("CREATE TABLE ezc_stats (nick VARCHAR(200), event VARCHAR(10), args TEXT, created DATETIME)")

        elif (rows < 3):
            print "ERROR: Database does not have all of the required tables, this can't be fixed."
            quit()
        
        
        
        
    
    # _reset()
    # resets core variables, ready for reconnect
    def _reset(self):
        self._pub_n_key = None
        self._room_id = 0
        self._msg_counter = 1

    # login()
    # sends login commands to the server for the email/password combination
    def login(self):
        account = acc.Account(self.email, self.password)
        if self.email and self.password:
            if account.is_logged_in:
                self._pub_n_key = account.n_key
                return True
            account.login()
            self._pub_n_key = account.n_key
            return account.is_logged_in
        return False

    # connect()
    # connect to the remote server
    def connect(self):
        # set up our error checking
        _error = None

        # no idea what why this is done
        if not self.users.client.nick.strip():
            self.users.client.nick = string_util.create_random_string(6, 25)  # adjust length

        try:
            # check if we are on Windows (nt) or Linux/UNIX/BSD (posix), etc
            if (os.name == "nt"):
                _is_win = True
            else:
                _is_win = False
            
            # configure the ezcapechat API
            params = ezcapechat.Params(self.room_name, self.users.client.nick,
                                       n_key=self._pub_n_key, proxy=self.proxy)

            # configure the RtmpClient
            self.connection = rtmp.RtmpClient(
                ip=params.ip,
                port=params.port,
                tc_url=params.tc_url,
                app=params.app,
                swf_url=params.swf_url,
                page_url=params.page_url,
                proxy=self.proxy,
                is_win=_is_win         # delete/set to false if not on windows
            )

            # connect to the remote server
            self.connection.connect(
                [
                    u'connect',             # application connect string?
                    u'',                    # ?
                    params.t1,              # t1
                    params.t2,              # t2
                    0,                      # ?
                    u'',                    # ?
                    u'',                    # ?
                    u'',                    # ?
                    self.room_name,         # room name
                    u'' + str(uuid.uuid4()).upper() + '-' + str(uuid.uuid4()).upper(), # local guid (meant to be system wide, but oh well)
                    u'' + str(uuid.uuid4()).upper() + '-' + str(uuid.uuid4()).upper(), # unknown guid
                    u'0.33',                # protocol version
                    u'',                    # room password
                    u'',                    # ?
                    False                   # disabled(flash_vars[0]) ?
                ]
            )
        except Exception as e:
            log.critical(e, exc_info=True)
            _error = e
        finally:
            if _error is not None:
                print ('connect error: %s' % _error)
            else:
                self.is_connected = True
                self.__callback()

    def disconnect(self):
        """ Disconnect from the remote server. """
        _error = None
        try:
            self.connection.shutdown()
        except Exception as e:
            log.error(e, exc_info=True)
            _error = 'disconnect error: %s' % e
        finally:
            if _error is not None and config.DEBUG_TO_CONSOLE:
                print (_error)
            self.is_connected = False
            self.connection = None

    def reconnect(self):
        """ Reconnect to the remote server. """
        if self.is_connected:
            self.disconnect()
        self._reset()
        time.sleep(config.RECONNECT_DELAY)  # increase reconnect delay?
        self.connect()

    def __callback(self):
        """ Callback loop reading packets/events from the stream. """

        log.debug('starting __callback loop, is_connected: %s' % self.is_connected)
        fails = 0

        while self.is_connected:
            try:
                amf_data = self.connection.amf()
                msg_type = amf_data['msg']
            except Exception as e:
                fails += 1
                log.error(e, exc_info=True)
                if fails == 2:
                    self.reconnect()
                    break
            else:
                fails = 0

                if config.DEBUG_TO_FILE:
                    log.debug(amf_data)

                if config.DEBUG_TO_CONSOLE:
                    print (amf_data)

                if msg_type == rtmp.rtmp_type.DT_COMMAND:

                    event_data = amf_data['command']
                    event = event_data[0]

                    if event == '_result':
                        self.on_result(event_data[3])

                    elif event == 'joinData':
                        self.on_join_data(event_data)

                    elif event == 'joinuser':
                        self.on_joinuser(event_data)

                    elif event == 'sendUserList':
                        self.on_send_userlist(event_data[3])

                    elif event == 'camList':
                        cam_data = event_data[3]
                        self.on_cam_list(cam_data)

                    elif event == 'updateRoomSecurity':
                        self.on_update_room_security(event_data)

                    elif event == 'receivePublicMsg':
                        self.on_receive_public_msg(event_data)

                    elif event == 'typingPM':
                        self.on_typing_pm(event_data)

                    elif event == 'pmReceive':
                        self.on_pm_receive(event_data)

                    elif event == 'removeuser':
                        self.on_removeuser(event_data[3])

                    elif event == 'statusUpdate':
                        self.on_status_update(event_data)

                    elif event == 'connectionOK':
                        self.on_connectin_ok()

                    elif event == 'ytVideoQueueAdd':
                        self.on_yt_video_queue_add(event_data)

                    elif event == 'ytVideoCurrent':
                        self.on_yt_video_current(event_data)

                    elif event == 'ytVideoQueue':
                        self.on_yt_video_queue(event_data)

                    else:
                        print ('Unknown event: `%s`, event data: %s' % (event, event_data))

    def on_result(self, data):
        """
        Default RTMP event.

        :param data: _result data containing information.
        :type data: object
        """
        if isinstance(data, rtmp.pyamf.ASObject):
            if 'code' in data:
                if data['code'] == rtmp.status.NC_CONNECT_REJECTED:
                    if 'application' in data:

                        # TODO: Implement reject event methods based on reject codes.
                        json_data = json.loads(data['application'])

                        reject_code = json_data['reject']
                        if reject_code == '0002':
                            print ('Closed, This room is closed.')
                        elif reject_code == '0003':
                            print ('Closed, That username is taken.')
                        elif reject_code == '0007':
                            print ('Chat version is out of date, please check the protocol version.')
                        elif reject_code == '0008':
                            print ('Room is password protected.')
                        elif reject_code == '0009':
                            print ('You are already in this room, would you like to disconnect the other session?')
                        elif reject_code == '0010':
                            print ('Busy, Server is busy, try again in a few seconds.')
                        elif reject_code == '0012':
                            print ('No Guests, This room does not allow guests.')
                            self.disconnect()
                        elif reject_code == '0013':
                            print ('Reload the page.')
                        elif reject_code == '0015':
                            print ('Unverified, You must verify your account before connecting.')
                        elif reject_code == '0016':
                            print ('Session Closed, Your other session was closed, you may now join the room.')

                        else:
                            print ('Error joining this room. Code: %s' % reject_code)

            if config.DEBUG_TO_CONSOLE:
                for k in data:
                    if isinstance(data, rtmp.pyamf.MixedArray):
                        for kk in data[k]:
                            print ('%s: %s' % (kk, data[k][kk]))
                    else:
                        print ('%s: %s' % (k, data[k]))
        else:
            if config.DEBUG_TO_CONSOLE:
                print (data)

    def on_join_data(self, data):
        """
        Received when a successful connection has been established to the remote stream.

        NOTE: This event is important, as it contains data
        the client needs to send to join the actual room.

        :param data: The join data as a list.
        :type data: list
        """
        self.users.client.key = data[7]         # unique user identifier ?
        self.users.client.join_time = data[11]  # join time as unix including milliseconds ?
        self._room_id = data[13]                # room id

        self.send_connection_ok()

        if config.DEBUG_TO_CONSOLE:
            print ('Join Data:')
            for i, v in enumerate(data):
                print ('\t[%s] - %s' % (i, v))

    def on_joinuser(self, data):
        """
        Received when a user joins the room.

        :param data: Information about the user.
        :type data: list
        """
        user_data = {
            'un': data[3],      # nick
            'ml': data[4],      # mod level
            'st': data[5],      # status related
            'id': data[6],      # ezcapechat user id
            'su': data[7]       # ?
        }
        if data[3] == self.users.client.nick:
            self.users.add_client_data(user_data)
        else:
            _user = self.users.add(data[3], user_data)
            print ('%s Joined the room.' % _user.nick)

            #BOT
            if (_user.nick.lower() in self.autogreet):
                self.send_public("%s, %s" % (_user.nick, self.autogreet[_user.nick.lower()]))

    def on_send_userlist(self, data):
        """

        :param data:
        :type data:
        """
        json_data = json.loads(data)
        for user_name in json_data:
            if user_name != self.users.client.nick:
                user_data = json.loads(json_data[user_name])
                _user = self.users.add(user_name, user_data)
                print ('\tJoins: %s' % _user)

    def on_cam_list(self, data):
        """

        add this data to the user object
        :param data:
        :type data:
        """
        json_data = json.loads(data)
        for k in json_data:
            print (json_data[k])

    def on_update_room_security(self, data):
        """

        related to the room settings.
        :param data:
        :type data:
        """
        if config.DEBUG_TO_CONSOLE:
            print ('Update room security:')
            for i, v in enumerate(data):
                print ('\t[%s] - %s' % (i, v))

    def on_receive_public_msg(self, data):
        """

        :param data:
        :type data:
        """
        if len(data) > 5:
            # data[3] = unix time stamp including milliseconds.
            user_name = data[4]
            msg = data[5]
            self.message_handler(user_name, msg)

    def message_handler(self, user_name, msg):
        """

        or overwrite om_receive_public_msg?
        :param user_name:
        :type user_name:
        :param msg:
        :type msg:
        """
        print ('%s: %s' % (user_name, msg))

        # check if there's a command
        if (msg[:1] == CONFIG.CMD):
            try:
                # throw the command to its own user_<cmd> in a thread, in case it takes a while
                cmd = threading.Thread(target = eval("self.user_%s" % lindex(msg, 0)[1:]), args = (user_name, lrange(msg, 1, -1)))
                cmd.start()
            except AttributeError as ex:
                print ("ERROR: " + str(ex))
        elif (user_name.lower() == self._mimic_user.lower()):
            self.send_public(msg)

    def on_typing_pm(self, data):
        """
        Received when a user is writing a private message to the client.

        :param data: Information about who is writing.
        :type data: list
        """
        # data[4] = ?
        print ('%s is typing a private message.' % data[3])

    def on_pm_receive(self, data):
        """
        Received when a user sends the client a private message.

        :param data: Private message information.
        :type data: list
        """
        # data[5] = receiver
        # data[6] = msg color
        # data[7] = ?
        print ('[PM] %s: %s' % (data[3], data[5]))
        
        try:
            # throw the command to its own user_<cmd> in a thread, in case it takes a while
            if (lindex(data[5], 0).lower() == "help"):
                cmd = threading.Thread(target = eval("self.user_%s" % lindex(data[5], 0)), args = (lrange(data[5], 1, -1)))
                cmd.start()
            elif ("has disabled pm" in data[5]):
                self.send_public("Invalid request: Unable to PM %s, PM disabled." % data[3])
        except AttributeError as ex:
            print ("ERROR: " + str(ex))


    def on_removeuser(self, username):
        """
        Received when a user leaves the room.

        :param username: The username of the user leaving the room.
        :type username: str
        """
        self.users.remove(username)
        print ('%s left the room.' % username)

    def on_status_update(self, data):
        """
        Received when a user changes their status. E.g /afk or /back.

        :param data:
        :type data: list
        """
        # TODO: Update User/Client object with this info
        print ('Status Update: %s' % data)

    def on_connectin_ok(self):
        """

        """
        print ('ConnectionOk: The connection to the room was established.')

    def on_yt_video_queue_add(self, data):
        """
        Received when a user adds a track to the playlist.

        :param data: List containing data about the track being added.
        :type data: list
        """
        # video_time = data[6]
        # queue number? = data[7]
        print ('%s added %s (%s) to the video queue.' % (data[3], data[5], data[4]))

    def on_yt_video_current(self, data):
        # offset? = data[5]
        # queue number? = data[6]
        print ('Current video: %s (%s)' % (data[4], data[3]))

    def on_yt_video_queue(self, data):
        # hmm. what*?.
        json_data = json.loads(data[3])
        print ('ytVideoQueue: %s' % json_data['c'])

    # Message construction.
    def send_connection_ok(self):
        """

        """
        self.connection.call(
            'connectionOK',
            [
                self._room_id,
                self.users.client.key,
                self.users.client.nick,
                u'1116348-1751801027-1858934494-1134291946'  # ?
            ]
        )

    # send_public(<message>)
    # sends a public message to the room
    def send_public(self, msg):
        self.connection.call(
            'send_public',
            [
                self._room_id,
                self.users.client.key,
                self.users.client.nick,
                msg,
                '0',
                '#87D37C',         # text color.
                '1',               # text sizes (0,1,2 or 3)
                self._msg_counter  # message counter.
            ]
        )

    # send_private(<nickname>, <message>)
    # sends a private message to someone in the room
    def send_private(self, nick, msg):
        self.connection.call(
            'pm',
            [
                self._room_id,
                self.users.client.key,
                self.users.client.nick,
                nick,
                msg,
                '#87D37C',         # text color.
                '1',               # text sizes (0,1,2 or 3)
                self._msg_counter  # message counter.
            ]
        )

    # send_secure_message(<message>)
    # assume this sends a secure message to the room
    def send_secure_message(self, msg):
        self.connection.call(
            'secure_message',
            [
                self._room_id,
                self.users.client.key,
                self.users.client.nick,
                msg,
                '0',            # text size?
                100,            # ?
                self._msg_counter
            ]
        )

    def send_tp_get_queue(self):
        """

        """
        self.connection.call(
            'tpGetQueue',
            [
                self._room_id,
                self.users.client.key,
                self.users.client.nick
            ]
        )

    def send_tp_get_current(self):
        """

        """
        self.connection.call(
            'tpGetCurrent',
            [
                self._room_id,
                self.users.client.key,
                self.users.client.nick
            ]
        )

    # send_topic(<new topic>)
    # updates the topic in the room
    def send_topic(self, new_topic):
        self.connection.call(
            'change_topic',
            [
                self._room_id,
                self.users.client.key,
                self.users.client.nick,
                new_topic
            ]
        )
        
    def send_kick(self, user):
        self.connection.call(
            'kick',
            [
                self._room_id,
                self.users.client.key,
                self.users.client.nick,
                user
            ]
        )



    def user_help(self, user, text):
        if (text == ""):
            self.send_private(user, "Here is a list of current commands. For further information use " + CONFIG.CMD + "help <command>")
            self.send_private(user, "leaderboard, stats, greet, kick, ban, topic, mimic")
        elif (text.lower() == "leaderboard"):
            self.send_private(user, CONFIG.CMD + "leaderboard will show you the current top talkers in the room.")
        elif (text.lower() == "stats"):
            self.send_private(user, CONFIG.CMD + "stats will show you user statistics from the channel including join, part, kick, lines and words.")
        elif (text.lower() == "greet"):
            self.send_private(user, CONFIG.CMD + "autogreet <nickname> <message> will set a new greeting for a user when they join, leave blank to delete.")
        elif (text.lower() == "kick"):
            self.send_private(user, CONFIG.CMD + "kick <nickname> will kick the user from the room.")
        elif (text.lower() == "ban"):
            self.send_private(user, CONFIG.CMD + "ban <nickname> will ban a user from the room.")
        elif (text.lower() == "topic"):
            self.send_private(user, CONFIG.CMD + "topic <new topic> will change the channel topic.")
        elif (text.lower() == "mimic"):
            self.send_private(user, CONFIG.CMD + "mimic <nickname> will mimic everything they say.")
        elif (text.lower() == "titties"):
            self.send_private(user, CONFIG.CMD + "titties will pull a random image from Google ;)")
        

    def user_autogreet(self, user, text):
        self.autogreet[lindex(text, 0).lower()] = lrange(text, 1, -1)
        self.send_public("%s, %s's greeting has now been set to: %s" % (user, lindex(text, 0), lrange(text, 1, -1)))
        write("autogreet.txt", "%s - %s > %s" % (user, lindex(text, 0), lrange(text, 1, -1)))
        
    def user_titties(self, user, text):
        url = {"https://bustliftcream.com/wp-content/uploads/2018/11/nudecoveringbreasts.png", "http://cdn.hornybank.com/6/072/22263712/16.jpg", "http://www.big-teen-tits.com/wp-content/uploads/sites/17/2018/02/bigteentits-jade-ftvgirls.jpg", "http://cdn.perfecttitsporn.com/2018-09-15/559939_14.jpg", "http://cdn.hotnakedgirls.net/2017-11-10/483578_10.jpg", "http://cdn1.teennudegirls.com/f0/e/f0e87a48e.jpg", "https://cdn.bignicetits.com/2017-11-11/470147_04.jpg"}
        self.send_public("I found this for you %s: %s" % (user, random.choice(list(url))))
        
    def user_cmd(self, user, text):
        self.send_cmd(text)
        
    def user_msg(self, user, text):
        self.send_private(user, text)
        
    def user_topic(self, user, text):
        self.send_topic(text)
        
    # !mimic <username>
    def user_mimic(self, user, text):
        if (lindex(text, 0).lower() == "off"):
            self._mimic_user = ""
            self.send_public("Cancelled.")
        else:
            self._mimic_user = lindex(text, 0)
            self.send_public("Following %s" % lindex(text, 0))
    
    def user_kick(self, user, text):
        self.send_kick(text)