#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright 2020 The Johns Hopkins University Applied Physics Laboratory
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
from setuptools import setup, find_packages

here = os.path.abspath(os.path.dirname(__file__))
def read(filename):
    with open(os.path.join(here, filename), 'r') as fh:
        return fh.read()

if __name__ == '__main__':
    setup(
        name='boss-oidc2',
        version='2.0.2',
        packages=find_packages(),
        url='https://github.com/jhuapl-boss/boss-oidc2',
        license="Apache Software License",
        author='JHUAPL Boss Team',
        description='Django Authentication OpenID Connect plugin for the Boss SSO',
        long_description=read('README.md'),
        classifiers=[
            'Environment :: Web Environment',
            'Development Status :: 5 - Production',
            'Framework :: Django',
            'Intended Audience :: Developers',
            'License :: OSI Approved :: Apache Software License',
            'Operating System :: OS Independent',
            'Natural Language :: English',
            'Programming Language :: Python :: 3',
            'Programming Language :: Python :: 3.12',
        ],
    )
