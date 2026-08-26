#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入口：主观层分析。依赖 run_analysis.py 的产出，失败不影响机械层。"""
import logging
import sys

from analyzer.ai.runner import run

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(run())
