#!/usr/bin/env python
#
# $File: manage_resource.py $
# $LastChangedDate: 2013-01-30 18:29:35 -0600 (Wed, 30 Jan 2013) $
# $Rev: 1663 $
#
# This file is part of variant_tools, a software application to annotate,
# summarize, and filter variants for next-gen sequencing ananlysis.
# Please visit http://varianttools.sourceforge.net for details.
#
# Copyright (C) 2011 Bo Peng (bpeng@mdanderson.org)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

import os
import sys
import argparse
import time
from variant_tools.utils import ResourceManager, env, calculateMD5
import base64

repoURL = 'bpeng1@bcbweb'
repoDir = '/var/www/html/Software/VariantTools/repository'

def remoteDo(cmd):
    if type(cmd) in (tuple, list):
        cmd = 'ssh {} "cd {}; {}"'.format(repoURL, repoDir, ';'.join(cmd))
    else:
        cmd = 'ssh {} "cd {}; {}"'.format(repoURL, repoDir, cmd)
    env.logger.info(cmd)
    os.system(cmd)

def deprecateFile(filename):
    # go to directory...
    d, f = os.path.split(filename)
    remoteDo('mkdir -p deprecated/{}'.format(d))
    remoteDo('mv {} deprecated/{}_{}'.format(filename, filename, time.strftime('%b%d', time.gmtime())))

def uploadFile(local_file, remote_file):
    # upload a local file to houstonbioinformatics.org
    # This is inefficient for multiple files (repeated login and out), but does not
    # really matter
    # go to directory...
    d, f = os.path.split(remote_file)
    deprecateFile(remote_file)
    cmd = 'scp {} {}:{}/{}'.format(local_file, repoURL, repoDir, remote_file)
    env.logger.info(cmd)
    os.system(cmd)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='''Manage variant tools resources''')
    parser.add_argument('--generate_local_manifest', nargs='?', const='~/.variant_tools',
        help='''Generate a manifest of local resource files. If a directory is not specified,
            $HOME/.variant_tools will be assumed. The manifest will be saved to 
            MANIFEST_local.txt.''')
    parser.add_argument('--list', nargs='*',
        help='''List all remote and local resources under ~/.variant_tools and mark them
            as identical, missing, new, and modified. If any argument is given, only
            resources with filename containing specified words are displayed.''')
    parser.add_argument('--update',  nargs='?', metavar='TYPE', 
        const='current', 
        choices=['current', 'all', 'existing', 'hg18', 'hg19', 'annotation', 'format',
           'snapshot', 'pipeline'],
        help='''Download resources of specified type, which can be 'current' (latest version
            of all resources excluding snapshots), 'all' (all resources including obsolete
            databases), 'existing' (only update resources that exist locally), 
            'hg18' or 'hg19' (all resources for reference genome hg18 or hg19),
            'annotation' (all current annotation databases), 'format' (all formats), and
            'snapshot' (all online snapshots). Identical resources that are available locally 
            (under ~/.variant_tools or runtime option $local_resource) are ignored. Note that
            option 'all' will download all versions of annotation databases which can be
            slow and take a lot of disk spaces.''')
    parser.add_argument('--upload', metavar='FILE', nargs='+',
        help='''Upload specified files to the server. The file should 
            be under the local resource directory ~/.variant_tools.''')
    parser.add_argument('--remove', metavar='FILENAME', nargs='+',
        help='''Remove specified files from the online manifest so that it will no
            longer be listed as part of the resource. The file itself, if exists, will
            be renamed but not removed from the server.''') 
    # this set up and use default temporary directory
    env.temp_dir = None
    args = parser.parse_args()
    env.logger = None  # no log file
    if args.generate_local_manifest is not None:
        manager = ResourceManager()
        # --generte_local_manifest without parameter will pass None, which will
        # use ~/.variant_tools.
        manager.scanDirectory(args.generate_local_manifest)
        manager.writeManifest('MANIFEST_local.txt')
        sys.stderr.write('Local manifest has been saved to MANIFEST_local.txt\n') 
    elif args.list is not None:
        manager = ResourceManager()
        manager.scanDirectory('~/.variant_tools', args.list)
        local_manifest = {x:y for x,y in manager.manifest.items()}
        manager.manifest.clear()
        manager.getRemoteManifest()
        remote_manifest = manager.manifest
        #
        # compare manifests
        for f, p in sorted(remote_manifest.iteritems()):
            if args.list and not all([x in f for x in args.list]):
                continue
            if f not in local_manifest:
                print('MISSING   {}'.format(f))
            elif p[0] != local_manifest[f][0] or p[1] != local_manifest[f][1]:
                print('MODIFIED  {}'.format(f))
            else:
                print('IDENTICAL {}'.format(f))
        for f, p in sorted(local_manifest.items()):
            if f not in remote_manifest:
                print('NEW       {}'.format(f))
    elif args.update:
        res = ResourceManager()
        res.getRemoteManifest()
        res.selectFiles(resource_type=args.update)
        res.excludeExistingLocalFiles()
        env.logger.info('{} files need to be downloaded or updated'.format(len(res.manifest)))
        res.downloadResources()
    elif args.upload:
        manager = ResourceManager()
        manager.getRemoteManifest()
        resource_dir = os.path.expanduser('~/.variant_tools')
        # get information about file
        for filename in args.upload:
            if filename.endswith('.DB'):
                env.logger.info('Ignore uncompressed database file {}'.format(filename))
                continue
            rel_path = os.path.relpath(filename, resource_dir)
            if rel_path in manager.manifest:
                filesize = os.path.getsize(filename)
                md5 = calculateMD5(filename, partial=True)
                refGenome = manager.getRefGenome(filename)
                comment = manager.getComment(filename).replace('\n', ' ').replace('\t', ' ').strip() 
                if (filesize, md5, refGenome, comment) == manager.manifest[rel_path]:
                    env.logger.info('Ignoring identical file {}'.format(rel_path))
                    continue
            manager.addResource(filename)
            uploadFile(filename, rel_path)
        manager.writeManifest('MANIFEST.tmp')
        uploadFile('MANIFEST.tmp', 'MANIFEST.txt')
    elif args.remove:
        manager = ResourceManager()
        manager.getRemoteManifest()
        removed_count = 0
        for filename in args.remove:
            if filename in manager.manifest:
                manager.manifest.pop(filename)
                deprecateFile(filename)
                env.logger.info('Remove {} from manifest'.format(filename))
                removed_count += 1
            else:
                env.logger.warning('{} does not exist in the manifest'.format(filename))
        # upload manifest
        if removed_count > 0:
            manager.writeManifest('MANIFEST.tmp')
            uploadFile('MANIFEST.tmp', 'MANIFEST.txt')
    else:
        env.logger.warning('No option has been provided. Please use -h to get a list of actions.')


