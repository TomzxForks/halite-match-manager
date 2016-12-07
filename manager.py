#!/bin/python3
#  Copyright 2016 Jude Hungerford
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#      Unless required by applicable law or agreed to in writing, software
#      distributed under the License is distributed on an "AS IS" BASIS,
#      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#      See the License for the specific language governing permissions and
#      limitations under the License.

import copy
import os
import random
import sys
import math
import sqlite3
import argparse
import datetime
import shutil
import skills
from skills import trueskill
from subprocess import Popen, PIPE

halite_command = "./halite"
replay_dir = "replays"
db_filename = "db.sqlite3"

def max_match_rounds(width, height):
    return math.sqrt(width * height) * 10.0

def update_player_skill(players, player_name, skill_data):
    """ Update the skill of one player """
    finished = False
    for player in players:
        if not finished:
            if player.name == str(player_name):
                player.mu = skill_data.mean
                player.sigma = skill_data.stdev
                player.update_skill()
                finished = True
                print("skill = %4f  mu = %3f  sigma = %3f  name = %s" % (player.skill, player.mu, player.sigma, str(player_name)))

def update_skills(players, ranks):
    """ Update player skills based on ranks from a match """
    teams = [skills.Team({player.name: skills.GaussianRating(player.mu, player.sigma)}) for player in players]
    match = skills.Match(teams, ranks)
    calc = trueskill.FactorGraphTrueSkillCalculator()
    game_info = trueskill.TrueSkillGameInfo()
    updated = calc.new_ratings(match, game_info)
    print ("Updating ranks")
    for team in updated:
        for i in team.keys():
            skill_data = team[i]
            update_player_skill(players, i, skill_data)

class Match:
    def __init__(self, players, width, height, seed, time_limit, keep_replays):
        self.map_seed = seed
        self.width = width
        self.height = height
        self.players = players
        self.paths = [player.path for player in players]
        self.finished = False
        self.results = [0 for _ in players]
        self.return_code = None
        self.results_string = ""
        self.replay_file = ""
        self.total_time_limit = time_limit
        self.timeouts = []
        self.num_players = len(players)
        self.keep_replay = keep_replays

    def __repr__(self):
        title1 = "Match between " + ", ".join([p.name for p in self.players]) + "\n"
        title2 = "Binaries are " + ", ".join(self.paths) + "\n"
        dims = "dimensions = " + str(self.width) + ", " + str(self.height) + "\n"
        results = "\n".join([str(i) + " " + j for i, j in zip(self.results, [p.name for p in self.players])]) + "\n"
        replay = self.replay_file + "\n\n"
        return title1 + title2 + dims + results + replay

    def get_command(self, halite_binary):
        dims = "-d " + str(self.width) + " " + str(self.height)
        quiet = "-q"
        seed = "-s " + str(self.map_seed)
        result = [halite_binary, dims, quiet, seed]
        return result + self.paths

    def run_match(self, halite_binary):
        command = self.get_command(halite_binary)
        p = Popen(command, stdin=None, stdout=PIPE, stderr=None)
        results, _ = p.communicate(None, self.total_time_limit)
        self.results_string = results.decode('ascii')
        self.return_code = p.returncode
        self.parse_results_string()
        update_skills(self.players, copy.deepcopy(self.results))
        if self.keep_replay:
            print("Keeping replay")
            if not os.path.exists(replay_dir):
                os.makedirs(replay_dir)
            shutil.move(self.replay_file, replay_dir)
        else:
            print("Deleting replay")
            os.remove(self.replay_file)

    def parse_results_string(self):
        lines = self.results_string.split("\n")
        if len(lines) < (2 + (2 * self.num_players)):
            raise ValueError("Not enough lines in match output")
        else:
            count = 0
            for line in lines:
                if count == self.num_players: # replay file and seed
                    self.replay_file = line.split(" ")[0]
                elif count == (self.num_players * 2) + 1: # timeouts
                    self.timeouts = (line.split(" "))
                elif count < self.num_players: # names
                    pass
                elif count < (self.num_players * 2) + 1:
                    token = line.split(" ")
                    rank = int(token[1])
                    player = int(token[0]) - 1
                    self.results[player] = rank
                count += 1

class Manager:
    def __init__(self, halite_binary, players=None, size_min=20, size_max=50, players_min=2, players_max=6, rounds=-1):
        self.halite_binary = halite_binary
        self.players = players
        self.size_min = size_min
        self.size_max = size_max
        self.players_min = players_min
        self.players_max = players_max
        self.rounds = rounds
        self.round_count = 0
        self.keep_replays = True
        self.priority_sigma = True
        self.db = Database()

    def run_round(self, players, width, height, seed):
        o_players = [self.players[i] for i in players]
        m = Match(o_players, width, height, seed, 2 * len(players) * max_match_rounds(width, height), self.keep_replays)
        print(m)
        m.run_match(self.halite_binary)
        print(m)
        self.save_players(o_players)
        self.db.update_player_ranks()

    def save_players(self, players):
        for player in players:
            print("Saving player %s with %f skill" % (player.name, player.skill))
            self.db.save_player(player)

    def pick_players_priority_sigma(self, num):
        open_set = [i for i in range(0, len(self.players))]
        players = []
        high_sigma = sorted(self.players, key=lambda x: x.sigma, reverse=True)[0]
        high_sigma_i = self.players.index(high_sigma)
        players.append(high_sigma_i)
        open_set.remove(high_sigma_i)
        count = 1
        while count < num:
            chosen = open_set[random.randint(0, len(open_set) - 1)]
            players.append(chosen)
            open_set.remove(chosen)
            count += 1
        return players

    def pick_players_no_priority(self, num):
        open_set = [i for i in range(0, len(self.players))]
        players = []
        count = 0
        while count < num:
            chosen = open_set[random.randint(0, len(open_set) - 1)]
            players.append(chosen)
            open_set.remove(chosen)
            count += 1
        return players

    def pick_players(self, num):
        if self.priority_sigma:
            return self.pick_players_priority_sigma(num)
        else:
            return self.pick_players_no_priority(num)

    def run_rounds(self, rounds):
        self.rounds = rounds
        while (self.rounds < 0) or (self.round_count < self.rounds):
            self.refresh_players()
            num_players = random.randint(2, min(self.players_max, len(self.players)))
            players = self.pick_players(num_players)
            size_w = random.randint((self.size_min / 5), (self.size_max / 5)) * 5
            size_h = size_w
            seed = random.randint(10000, 2073741824)
            print ("running match...\n")
            self.run_round(players, size_w, size_h, seed)
            self.round_count += 1

    def refresh_players(self):
        player_records = self.db.retrieve("select * from players where active > 0")
        players = [parse_player_record(player) for player in player_records]
        if len(players) < 2:
            print("Not enough players for a game. Need at least " + str(self.players_min) + ", only have " + str(len(players)))
            print("use the -h flag to get help")
            sys.os.exit(-1)
        self.players = players

    def add_player(self, name, path):
        p = self.db.get_player((name,))
        if len(p) == 0:
            self.db.add_player(name, path)
        else:
            print ("Bot name %s already used, no bot added" %(name))

class Database:
    def __init__(self, filename=db_filename):
        self.db = sqlite3.connect(filename)
        self.recreate()
        try:
            self.latest = int(self.db.retrieve("select id from games order by id desc limit 1;",())[0][0])
        except:
            self.latest = 1

    def __del__(self):
        try:
            self.db.close()
        except: pass

    def now(self):
        return datetime.datetime.utcnow().strftime("%d.%m.%Y %H:%M:%S") #asctime()
    def recreate(self):
        cursor = self.db.cursor()
        try:
            cursor.execute("create table games(id integer, players text, map integer, datum date, turns integer default 0)")
            cursor.execute("create table players(id integer primary key autoincrement, name text unique, path text, lastseen date, rank integer default 1000, skill real default 0.0, mu real default 50.0, sigma real default 13.3,ngames integer default 0, active integer default 1)")
            self.db.commit()
        except:
            pass

    def update_deferred( self, sql, tup=() ):
        cursor = self.db.cursor()
        cursor.execute(sql,tup)

    def update( self, sql, tup=() ):
        self.update_deferred(sql,tup)
        self.db.commit()

    def retrieve( self, sql, tup=() ):
        cursor = self.db.cursor()
        cursor.execute(sql,tup)
        return cursor.fetchall()

    def add_match( self, match ):
        self.latest += 1
        players = ", ".join(match.paths)
        self.update("insert into games values(?,?,?,?,?,?)", (self.latest,players,match.map_seed,self.now(),turns))

    def add_player(self, name, path):
        self.update("insert into players values(?,?,?,?,?,?,?,?,?,?)", (None, name, path, self.now(), 1000, 0.0, 50.0, 50.0/3.0, 0, True))

    def delete_player(self, name):
        self.update("delete from players where name=?", [name])

    def get_player( self, names ):
        sql = "select * from players where name=?"
        for n in names[1:]:
            sql += " or name=?"
        return self.retrieve(sql, names )

    def save_player(self, player):
        self.update_player_skill(player.name, player.skill, player.mu, player.sigma)

    def update_player_skill(self, name, skill, mu, sigma ):
        self.update("update players set ngames=ngames+1,lastseen=?,skill=?,mu=?,sigma=? where name=?", (self.now(), skill, mu, sigma, name))

    def update_player_rank( self, name, rank ):
        self.update("update players set rank=? where name=?", (rank, name))

    def update_player_ranks(self):
        for i, p in enumerate(self.retrieve("select name from players order by skill desc",())):
            self.update_player_rank( p[0], i+1 )

    def activate_player(self, name):
        self.update("update players set active=? where name=?", (1, name))

    def deactivate_player(self, name):
        self.update("update players set active=? where name=?", (0, name))


class Player:
    def __init__(self, name, path, last_seen = "", rank = 1000, skill = 0.0, mu = 50.0, sigma = (50.0 / 3.0), ngames = 0, active = 1):
        self.name = name
        self.path = path
        self.last_seen = last_seen
        self.rank = rank
        self.skill = skill
        self.mu = mu
        self.sigma = sigma
        self.ngames = ngames
        self.active = active

    def __repr__(self):
        return "%s\t%s\t%d\t%3f\t%3f\t%3f\t%d\t%d" % (self.name, self.last_seen, self.rank, self.skill, self.mu, self.sigma, self.ngames, self.active)

    def update_skill(self):
        self.skill = self.mu - (self.sigma * 3)

def parse_player_record (player):
    (player_id, name, path, last_seen, rank, skill, mu, sigma, ngames, active) = player
    return Player(name, path, last_seen, rank, skill, mu, sigma, ngames, active)


class Commandline:
    def __init__(self):
        self.manager = Manager(halite_command)
        self.parser = argparse.ArgumentParser()
        self.no_args = False
        self.exclude_inactive = False

        subparsers = self.parser.add_subparsers(title = 'commands', dest = 'command')

        addParser = subparsers.add_parser('add', description = "Add a new bot with a name")
        addParser.add_argument('name', help = "Name of the bot")
        addParser.add_argument('botPath', help = "Specify the path for a new bot")
        addParser.set_defaults(func=self.act_add)

        deleteParser = subparsers.add_parser('delete', description = "Delete the named bot")
        deleteParser.add_argument('name', help = "Name of the bot")
        deleteParser.set_defaults(func=self.act_delete)

        activateParser = subparsers.add_parser('activate', description = "Activate the named bot")
        activateParser.add_argument('name', help = "Name of the bot")
        activateParser.set_defaults(func=self.act_activate)

        deactivateParser = subparsers.add_parser('deactivate', description = "Deactivate the named bot")
        deactivateParser.add_argument('name', help = "Name of the bot")
        deactivateParser.set_defaults(func=self.act_deactivate)

        ranksParser = subparsers.add_parser('ranks', description = "Show a list of all bots, ordered by skill")
        ranksParser.add_argument("-t", "--tsv", dest="tsv",
                                 action = "store_true", default = False,
                                 help = "Show a list of all bots ordered by skill, with headings in TSV format like the rest of the data")
        ranksParser.add_argument("-E", "--exclude-inactive", dest="excludeInactive",
                                 action = "store_true", default = False,
                                 help = "Exclude inactive bots from ranking table")
        ranksParser.set_defaults(func=self.act_ranks)

        matchParser = subparsers.add_parser('match', description = "Run a single match")
        matchParser.add_argument("-f", "--forever", dest="forever",
                                 action = "store_true", default = False,
                                 help = "Run games forever (or until interrupted)")
        matchParser.add_argument("-n", "--no-replays", dest="deleteReplays",
                                 action = "store_true", default = False,
                                 help = "Do not store replays")
        matchParser.add_argument("-e", "--equal-priority", dest="equalPriority",
                                 action = "store_true", default = False,
                                 help = "Equal priority for all active bots (otherwise highest sigma will always be selected)")
        matchParser.set_defaults(func=self.act_match)

    def parse(self, args):
        if len(args) == 0:
            self.no_args = True
        self.args = self.parser.parse_args(args)
        if args:
            self.args.func(self.args)
        else:
            self.parser.print_help()

    def add_bot(self, bot, path):
        self.manager.add_player(bot, path)

    def delete_bot(self, bot):
        self.manager.db.delete_player(bot)

    def valid_botfile(self, path):
        return True

    def run_matches(self, rounds):
        self.manager.run_rounds(rounds)

    def player_list_sql(self):
        if self.exclude_inactive:
            return "select * from players where active > 0 order by skill desc"
        else:
            return "select * from players order by skill desc"

    def act_add(self, args):
        print("Adding new bot %s..." %(args.name))
        if self.valid_botfile(args.botPath):
            self.add_bot(args.name, args.botPath)

    def act_delete(self, args):
        print("Deleting bot %s..." %(args.name))
        self.delete_bot(args.name)

    def act_activate(self, args):
        print("Activating bot %s..." %(args.name))
        self.manager.db.activate_player(args.name)

    def act_deactivate(self, args):
        print("Deactivating bot %s..." %(args.name))
        self.manager.db.deactivate_player(args.name)

    def act_ranks(self, args):
        if args.excludeInactive:
            print("exclude_inactive = True")
            self.exclude_inactive = True

        if not args.tsv:
            print ("%s\t\t%s\t\t%s\t%s\t\t%s\t\t%s\t\t%s\t%s" % ("name", "last_seen", "rank", "skill", "mu", "sigma", "ngames", "active"))
            sql = self.player_list_sql()
            for p in self.manager.db.retrieve(sql):
                print(str(parse_player_record(p)))
        else:
            print ("%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s" % ("name", "last_seen", "rank", "skill", "mu", "sigma", "ngames", "active"))
            sql = self.player_list_sql()
            for p in self.manager.db.retrieve(sql):
                print(str(parse_player_record(p)))

    def act_match(self, args):
        if args.deleteReplays:
            print("keep_replays = False")
            self.manager.keep_replays = False
        if args.equalPriority:
            print("priority_sigma = False")
            self.manager.priority_sigma = False

        if args.forever:
            print ("Running matches until interrupted. Press Ctrl+C to stop.")
            self.run_matches(-1)
        else:
            print ("Running a single match.")
            self.run_matches(1)

cmdline = Commandline()
cmdline.parse(sys.argv[1:])
