#!/bin/sh
set -e
export TZ="${TZ:-Asia/Shanghai}"
exec python scheduler.py
