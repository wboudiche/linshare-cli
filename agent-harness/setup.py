#!/usr/bin/env python3
"""
setup.py for cli-anything-linshare

Install with: pip install -e .
"""

import os

from setuptools import find_namespace_packages, setup

README = "cli_anything/linshare/README.md"

setup(
    name="cli-anything-linshare",
    version="0.1.0",
    author="Walid Boudiche",
    url="https://github.com/wboudiche/linshare-cli",
    description="CLI harness for LinShare - share documents through the LinShare user REST API. "
    "Requires: a LinShare 6.5+ server account",
    long_description=open(README, encoding="utf-8").read() if os.path.exists(README) else "LinShare CLI.",
    long_description_content_type="text/markdown",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "License :: OSI Approved :: GNU Affero General Public License v3",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Communications :: File Sharing",
    ],
    python_requires=">=3.10",
    install_requires=[
        "click>=8.0.0",
        "requests>=2.28.0",
        "prompt-toolkit>=3.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "responses>=0.23.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "cli-anything-linshare=cli_anything.linshare.linshare_cli:main",
            "linshare=cli_anything.linshare.linshare_cli:main",
        ],
    },
    package_data={
        "cli_anything.linshare": ["README.md"],
    },
    include_package_data=True,
    zip_safe=False,
)
