#!/bin/bash
for i in bots/*
do
   botname=${i/bots\//}
  ./manager.py add "$botname" -p "$i"/MyBot.native
done
