#!/usr/bin/env python3
#
import os
import sys
import subprocess
import glob
import argparse
import logging
import shutil
import tarfile
import copy
import gzip
import bz2
import zipfile
import time
import re
from collections import defaultdict, namedtuple
#
# Runtime environment
#
class RuntimeEnvironment(object):
    '''Define the runtime environment of the pipeline'''
    # the following makes RuntimeEnvironment a singleton class
    _instance = None
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(RuntimeEnvironment, cls).__new__(cls, *args, 
                **kwargs)
        return cls._instance

    def __init__(self):
        self._max_jobs = 1
        #
        # file and screen logging
        self._logger = None
        #
        # working directory
        self._working_dir = None
        #
        # additional parameters for args
        self.options = defaultdict(str)
        #
        # running_jobs implements a simple multi-processing queue system. 
        # This variable holds a JOB tuple of the running jobs.
        #
        # Functions:
        #   BaseVariantCaller.call(cmd, upon_succ, wait=False)
        #      add an entry until there is less than self._max_jobs running jobs
        #   BaseVariantCaller.poll()
        #      check the number of running jobs
        #   BaseVariantCaller.wait()
        #      wait till all jobs are completed
        self.running_jobs = []
    
    #
    # max number of jobs
    #
    #
    def _setMaxJobs(self, x):
        try:
            self._max_jobs = int(x)
        except Exception as e:
            sys.exit('Failed to set max jobs: {}'.format(e))

    jobs = property(lambda self:self._max_jobs, _setMaxJobs)

    class ColoredFormatter(logging.Formatter):
        ''' A logging format with colored output, which is copied from
        http://stackoverflow.com/questions/384076/how-can-i-make-the-python-logging-output-to-be-colored
        '''
        def __init__(self, msg):
            logging.Formatter.__init__(self, msg)
            self.LEVEL_COLOR = {
                'DEBUG': 'BLUE',
                'WARNING': 'PURPLE',
                'ERROR': 'RED',
                'CRITICAL': 'RED_BG',
                }
            self.COLOR_CODE={
                'ENDC':0,  # RESET COLOR
                'BOLD':1,
                'UNDERLINE':4,
                'BLINK':5,
                'INVERT':7,
                'CONCEALD':8,
                'STRIKE':9,
                'GREY30':90,
                'GREY40':2,
                'GREY65':37,
                'GREY70':97,
                'GREY20_BG':40,
                'GREY33_BG':100,
                'GREY80_BG':47,
                'GREY93_BG':107,
                'DARK_RED':31,
                'RED':91,
                'RED_BG':41,
                'LIGHT_RED_BG':101,
                'DARK_YELLOW':33,
                'YELLOW':93,
                'YELLOW_BG':43,
                'LIGHT_YELLOW_BG':103,
                'DARK_BLUE':34,
                'BLUE':94,
                'BLUE_BG':44,
                'LIGHT_BLUE_BG':104,
                'DARK_MAGENTA':35,
                'PURPLE':95,
                'MAGENTA_BG':45,
                'LIGHT_PURPLE_BG':105,
                'DARK_CYAN':36,
                'AUQA':96,
                'CYAN_BG':46,
                'LIGHT_AUQA_BG':106,
                'DARK_GREEN':32,
                'GREEN':92,
                'GREEN_BG':42,
                'LIGHT_GREEN_BG':102,
                'BLACK':30,
            }

        def colorstr(self, astr, color):
            return '\033[{}m{}\033[{}m'.format(self.COLOR_CODE[color], astr, 
                self.COLOR_CODE['ENDC'])

        def format(self, record):
            record = copy.copy(record)
            levelname = record.levelname
            if levelname in self.LEVEL_COLOR:
                record.levelname = self.colorstr(levelname, self.LEVEL_COLOR[levelname])
                record.name = self.colorstr(record.name, 'BOLD')
                record.msg = self.colorstr(record.msg, self.LEVEL_COLOR[levelname])
            return logging.Formatter.format(self, record)

    def _setLogger(self, logfile=None):
        '''Create a logger with colored console output, and a log file if a
        filename is provided.'''
        # create a logger
        self._logger = logging.getLogger()
        self._logger.setLevel(logging.DEBUG)
        # output to standard output
        cout = logging.StreamHandler()
        cout.setLevel(logging.INFO)
        cout.setFormatter(RuntimeEnvironment.ColoredFormatter('%(levelname)s: %(message)s'))
        self._logger.addHandler(cout)
        if logfile is not None:
            # output to a log file
            ch = logging.FileHandler(logfile, 'a')
            ch.setLevel(logging.DEBUG)
            ch.setFormatter(logging.Formatter('%(asctime)s: %(levelname)s: %(message)s'))
            self._logger.addHandler(ch)
    #
    logger = property(lambda self: self._logger, _setLogger)

    def _setWorkingDir(self, working_dir=None):
        if working_dir is None:
            raise RuntimeError('Invalid working directory.')
        if not os.path.isdir(working_dir):
            os.makedirs(working_dir)
        self._working_dir = working_dir
        if self._logger is not None:
            self._logger.info('Setting working directory to {}'
                .format(self._working_dir))
        
    working_dir = property(lambda self: self._working_dir, _setWorkingDir)

# create a runtime environment object
env = RuntimeEnvironment()


#
# A simple job management scheme 
#
# NOTE:
#   subprocess.PIPE cannot be used for NGS commands because they tend to send
#   a lot of progress output to stderr, which might block PIPE and cause the
#   command itself to fail, or stall (which is even worse).
#
JOB = namedtuple('JOB', 'proc cmd upon_succ start_time stdout stderr name')

def elapsed_time(start):
    '''Return the elapsed time in human readable format since start time'''
    second_elapsed = int(time.time() - start)
    days_elapsed = second_elapsed // 86400
    return ('{} days '.format(days_elapsed) if days_elapsed else '') + \
        time.strftime('%H:%M:%S', time.gmtime(second_elapsed % 86400))
 
def run_command(cmd, name=None, upon_succ=None, wait=True):
    '''Call an external command, raise an error if it fails.
    If upon_succ is specified, the specified function and parameters will be
    evalulated after the job has been completed successfully.
    If a name is given, stdout and stderr will be sent to env.working_dir, 
    name.out and name.err. Otherwise, stdout and stderr will be ignored.
    '''
    # merge mulit-line command into one line and remove extra white spaces
    cmd = ' '.join(cmd.split())
    if name is None:
        proc_out = subprocess.DEVNULL
        proc_err = subprocess.DEVNULL
    else:
        name = name.replace('/', '_')
        proc_out = open(os.path.join(env.working_dir, name + '.out'), 'w')
        proc_err = open(os.path.join(env.working_dir, name + '.err'), 'w')
    if wait or env.jobs == 1:
        try:
            s = time.time()
            env.logger.info('Running {}'.format(cmd))
            proc = subprocess.Popen(cmd, shell=True, stdout=proc_out, stderr=proc_err)
            retcode = proc.wait()
            if name is not None:
                proc_out.close()
                proc_err.close()
            if retcode < 0:
                env.logger.error('Command {} was terminated by signal {} after executing {}'
                    .format(cmd, -retcode, elapsed_time(s)))
                sys.exit(1)
            elif retcode > 0:
                if name is not None:
                    with open(os.path.join(env.working_dir, name + '.err')) as err:
                        for line in err.read().split('\n')[-20:]:
                            env.logger.error(line)
                env.logger.error("Command {} returned {} after executing {}"
                    .format(cmd, retcode, elapsed_time(s)))
                sys.exit(1)
            env.logger.info('Command {} completed successfully in {}'
                .format(cmd, elapsed_time(s)))
        except OSError as e:
            env.logger.error("Execution of command {} failed: {}".format(cmd, e))
            sys.exit(1)
        # everything is OK
        if upon_succ:
            # call the function (upon_succ) using others as parameters.
            upon_succ[0](*(upon_succ[1:]))
    else:
        # wait for empty slot to run the job
        while True:
            if poll_jobs() >= env.jobs:
                time.sleep(5)
            else:
                break
        # there is a slot, start running
        proc = subprocess.Popen(cmd, shell=True, stdout=proc_out, stderr=proc_err)
        env.running_jobs.append(JOB(proc=proc, cmd=cmd, upon_succ=upon_succ,
            start_time=time.time(), stdout=proc_out, stderr=proc_err, name=name))
        env.logger.info('Running {}'.format(cmd))

def poll_jobs():
    '''check the number of running jobs.'''
    count = 0
    for idx, job in enumerate(env.running_jobs):
        if job is None:
            continue
        ret = job.proc.poll()
        if ret is None:  # still running
            count += 1
            continue
        #
        # job completed, close redirected stdout and stderr
        if job.stderr != subprocess.DEVNULL:
            try:
                job.stdout.close()
                job.stderr.close()
            except:
                pass
        #
        if ret < 0:
            env.logger.error("Command {} was terminated by signal {} after executing {}"
                .format(job.cmd, -ret, elapsed_time(job.start_time)))
            sys.exit(1)
        elif ret > 0:
            if job.name is not None:
                with open(os.path.join(env.working_dir, job.name + '.err')) as err:
                    for line in err.read().split('\n')[-50:]:
                        env.logger.error(line)
            env.logger.error('Execution of command {} failed after {} (return code {}).'
                .format(job.cmd, elapsed_time(job.start_time), ret))
            sys.exit(1)
        else:
            if job.name is not None:
                with open(os.path.join(env.working_dir, job.name + '.err')) as err:
                    for line in err.read().split('\n')[-10:]:
                        env.logger.info(line)
            # finish up
            if job.upon_succ:
                # call the upon_succ function
                job.upon_succ[0](*(job.upon_succ[1:]))
            env.logger.info('Command {} completed successfully in {}'
                .format(job.cmd, elapsed_time(job.start_time)))
            #
            env.running_jobs[idx] = None
    return count

def wait_all():
    '''Wait for all pending jobs to complete'''
    while poll_jobs() > 0:
        # sleep ten seconds before checking job status again.
        time.sleep(10)
    env.running_jobs = []

#
# Check the existence of commands
#
def checkCmd(cmd):
    '''Check if a cmd exist'''
    if not hasattr(shutil, 'which'):
        env.logger.error('Please use Python 3.3 or higher for the use of shutil.which function')
        sys.exit(1)
    if shutil.which(cmd) is None:
        env.logger.error('Command {} does not exist. Please install it and try again.'
            .format(cmd))
        sys.exit(1)

def checkPicard():
    '''Check if picard is available, set PICARD_PATH if the path is specified in CLASSPATH'''
    if env.options['PICARD_PATH']:
        if not os.path.isfile(os.path.join(os.path.expanduser(env.options['PICARD_PATH']), 'SortSam.jar')):
            env.logger.error('Specified PICARD_PATH {} does not contain picard jar files.'
                .format(env.options['PICARD_PATH']))
            sys.exit(1)
    elif 'CLASSPATH' in os.environ:
        if not any([os.path.isfile(os.path.join(os.path.expanduser(x), 'SortSam.jar')) 
                for x in os.environ['CLASSPATH'].split(':')]):
            env.logger.error('$CLASSPATH ({}) does not contain a path that contain picard jar files.'
                .format(os.environ['CLASSPATH']))
            sys.exit(1)
        for x in os.environ['CLASSPATH'].split(':'):
            if os.path.isfile(os.path.join(os.path.expanduser(x), 'SortSam.jar')):
                env.logger.info('Using picard under {}'.format(x))
                env.options['PICARD_PATH'] = os.path.expanduser(x)
                break
    else:
        env.logger.error('Please either specify path to picard using option '
            'PICARD_PATH=path, or set it in environment variable $CLASSPATH.')
        sys.exit(1)

def checkGATK():
    '''Check if GATK is available, set GATK_PATH from CLASSPATH if the path
        is specified in CLASSPATH'''
    if env.options['GATK_PATH']:
        if not os.path.isfile(os.path.join(os.path.expanduser(env.options['GATK_PATH']),
                'GenomeAnalysisTK.jar')):
            env.logger.error('Specified GATK_PATH {} does not contain GATK jar files.'
                .format(env.options['GATK_PATH']))
            sys.exit(1)
    elif 'CLASSPATH' in os.environ:
        if not any([os.path.isfile(os.path.join(os.path.expanduser(x), 'GenomeAnalysisTK.jar'))
                for x in os.environ['CLASSPATH'].split(':')]):
            env.logger.error('$CLASSPATH ({}) does not contain a path that contain GATK jar files.'
                .format(os.environ['CLASSPATH']))
            sys.exit(1)
        else:
            for x in os.environ['CLASSPATH'].split(':'):
                if os.path.isfile(os.path.join(os.path.expanduser(x), 'GenomeAnalysisTK.jar')):
                    env.logger.info('Using GATK under {}'.format(x))
                    env.options['GATK_PATH'] = os.path.expanduser(x)
                    break
    else:
        env.logger.error('Please either specify path to GATK using option '
            'GATK_PATH=path, or set it in environment variable $CLASSPATH.')
        sys.exit(1) 

# 
# def checkPySam():
#     '''Check if PySam (a python wrapper to samtools) is installed.'''
#     try:
#         import pysam
#     except ImportError:
#         env.logger.error('Please install pysam for python3.3 and try again.')
# 
#
# Utility functions
# 
def downloadFile(URL, dest, quiet=False):
    '''Download a file from URL and save to dest'''
    # for some strange reason, passing wget without shell=True can fail silently.
    env.logger.info('Downloading {}'.format(URL))
    if os.path.isfile(dest):
        env.logger.warning('Using existing downloaded file {}.'.format(dest))
        return dest
    p = subprocess.Popen('wget {} -O {}_tmp {}'
        .format('-q' if quiet else '', dest, URL), shell=True)
    ret = p.wait()
    if ret == 0 and os.path.isfile(dest + '_tmp'):
        os.rename(dest + '_tmp', dest)
        return dest
    else:
        try:
            os.remove(dest + '_tmp')
        except OSError:
            pass
        raise RuntimeError('Failed to download {} using wget'.format(URL))


def isBamPairedEnd(bamfile):
    # FIXME: check if a bam file has paired end reads
    # we need pysam for this but pysam does not yet work for Python 3.3.
    return True

def fastqVersion(fastq_file):
    '''Detect the version of input fastq file. This can be very inaccurate'''
    #
    # This function assumes each read take 4 lines, and the last line contains
    # quality code. It collects about 1000 quality code and check their range,
    # and use it to determine if it is Illumina 1.3+
    #
    qual_scores = ''
    with open(fastq_file) as fastq:
        while len(qual_scores) < 1000:
            try:
                line = fastq.readline()
            except Exception as e:
                env.logger.error('Failed to read fastq file {}: {}'
                    .format(fastq_file, e))
                sys.exit(1)
            if not line.startswith('@'):
                raise ValueError('Wrong FASTA file {}'.format(fastq_file))
            line = fastq.readline()
            line = fastq.readline()
            if not line.startswith('+'):
                env.logger.warning(
                    'Suspiciout FASTA file {}: third line does not start with "+".'
                    .foramt(fastq_file))
                return 'Unknown'
            line = fastq.readline()
            qual_scores += line.strip()
    #
    min_qual = min([ord(x) for x in qual_scores])
    max_qual = max([ord(x) for x in qual_scores])
    env.logger.debug('FASTA file with quality score ranging {} to {}'
        .format(min_qual, max_qual))
    # Sanger qual score has range Phred+33, so 33, 73 with typical score range 0 - 40
    # Illumina qual scores has range Phred+64, which is 64 - 104 with typical score range 0 - 40
    if min_qual >= 64 or max_qual > 90:
        # option -I is needed for bwa if the input is Illumina 1.3+ read format (quliaty equals ASCII-64).
        return 'Illumina 1.3+'
    else:
        # no option is needed for bwa
        return 'Sanger'

def existAndNewerThan(ofiles, ifiles):
    '''Check if ofiles is newer than ifiles. The oldest timestamp
    of ofiles and newest timestam of ifiles will be used if 
    ofiles or ifiles is a list.'''
    if type(ofiles) == list:
        if not all([os.path.isfile(x) for x in ofiles]):
            return False
    else:
        if not os.path.isfile(ofiles):
            return False
    #
    if type(ofiles) == list:
        output_timestamp = min([os.path.getmtime(x) for x in ofiles])
    else:
        output_timestamp = os.path.getmtime(ofiles)
    #
    if type(ifiles) == list:
        input_timestamp = max([os.path.getmtime(x) for x in ifiles])
    else:
        input_timestamp = os.path.getmtime(ifiles)
    #
    if output_timestamp - input_timestamp < 10:
        env.logger.warning(
            'Existing output file {} is ignored because it is newer than input file.'
            .format(', '.join(ofiles) if type(ofiles) == list else ofiles))
        return False
    else:
        # newer by at least 10 seconds.
        return True

def TEMP(filename):
    '''Temporary output of filename'''
    # turn path/filename.ext to path/filename_tmp.ext
    return '_tmp.'.join(filename.rsplit('.', 1))

def decompress(filename, dest_dir=None):
    '''If the file ends in .tar.gz, .tar.bz2, .bz2, .gz, .tgz, .tbz2, decompress it to
    dest_dir (current directory if unspecified), and return a list of files. Uncompressed
    files will be returned untouched.'''
    mode = None
    if filename.lower().endswith('.tar.gz') or filename.lower().endswith('.tar.bz2'):
        mode = 'r:gz'
    elif filename.lower().endswith('.tbz2') or filename.lower().endswith('.tgz'):
        mode = 'r:bz2'
    elif filename.lower().endswith('.tar'):
        mode = 'r'
    elif filename.lower().endswith('.gz'):
        dest_file = os.path.join('.' if dest_dir is None else dest_dir,
            os.path.basename(filename)[:-3])
        if existAndNewerThan(ofiles=dest_file, ifiles=filename):
            env.logger.warning('Using existing decompressed file {}'.format(dest_file))
        else:
            env.logger.info('Decompressing {} to {}'.format(filename, dest_file))
            with gzip.open(filename, 'rb') as gzinput, open(dest_file + '_tmp', 'wb') as output:
                content = gzinput.read(10000000)
                while content:
                    output.write(content)
                    content = gzinput.read(10000000)
            # only rename the temporary file to the right one after finishing everything
            # this avoids corrupted files
            os.rename(dest_file + '_tmp', dest_file)
        return [dest_file]
    elif filename.lower().endswith('.bz2'):
        dest_file = os.path.join('.' if dest_dir is None else dest_dir, os.path.basename(filename)[:-4])
        if existAndNewerThan(ofiles=dest_file, ifiles=filename):
            env.logger.warning('Using existing decompressed file {}'.format(dest_file))
        else:
            env.logger.info('Decompressing {} to {}'.format(filename, dest_file))
            with bz2.open(filename, 'rb') as bzinput, open(dest_file + '_tmp', 'wb') as output:
                content = bzinput.read(10000000)
                while content:
                    output.write(content)
                    content = bzinput.read(10000000)
            # only rename the temporary file to the right one after finishing everything
            # this avoids corrupted files
            os.rename(dest_file + '_tmp', dest_file)
        return [dest_file]
    elif filename.lower().endswith('.zip'):
        bundle = zipfile.ZipFile(filename)
        dest_dir = '.' if dest_dir is None else dest_dir
        bundle.extractall(dest_dir)
        env.logger.info('Decompressing {} to {}'.format(filename, dest_dir))
        return [os.path.join(dest_dir, name) for name in bundle.namelist()]
    #
    # if it is a tar file
    if mode is not None:
        env.logger.info('Extracting fastq sequences from tar file {}'
            .format(filename))
        #
        # MOTE: open a compressed tar file can take a long time because it needs to scan
        # the whole file to determine its content. I am therefore creating a manifest
        # file for the tar file in the dest_dir, and avoid re-opening when the tar file
        # is processed again.
        manifest = os.path.join( '.' if dest_dir is None else dest_dir,
            os.path.basename(filename) + '.manifest')
        all_extracted = False
        dest_files = []
        if existAndNewerThan(ofiles=manifest, ifiles=filename):
            all_extracted = True
            for f in [x.strip() for x in open(manifest).readlines()]:
                dest_file = os.path.join( '.' if dest_dir is None else dest_dir, os.path.basename(f))
                if existAndNewerThan(ofiles=dest_file, ifiles=filename):
                    dest_files.append(dest_file)
                    env.logger.warning('Using existing extracted file {}'.format(dest_file))
                else:
                    all_extracted = False
        #
        if all_extracted:
            return dest_files
        #
        # create a temporary directory to avoid corrupted file due to interrupted decompress
        try:
            os.mkdir('tmp' if dest_dir is None else os.path.join(dest_dir, 'tmp'))
        except:
            # directory might already exist
            pass
        #
        dest_files = []
        with tarfile.open(filename, mode) as tar:
            files = tar.getnames()
            # save content to a manifest
            with open(manifest, 'w') as manifest:
                for f in files:
                    manifest.write(f + '\n')
            for f in files:
                # if there is directory structure within tar file, decompress all to the current directory
                dest_file = os.path.join( '.' if dest_dir is None else dest_dir, os.path.basename(f))
                dest_files.append(dest_file)
                if existAndNewerThan(ofiles=dest_file, ifiles=filename):
                    env.logger.warning('Using existing extracted file {}'.format(dest_file))
                else:
                    env.logger.info('Extracting {} to {}'.format(f, dest_file))
                    tar.extract(f, 'tmp' if dest_dir is None else os.path.join(dest_dir, 'tmp'))
                    # move to the top directory with the right name only after the file has been properly extracted
                    shutil.move(os.path.join('tmp' if dest_dir is None else os.path.join(dest_dir, 'tmp'), f), dest_file)
            # set dest_files to the same modification time. This is used to
            # mark the right time when the files are created and avoid the use
            # of archieved but should-not-be-used files that might be generated later
            [os.utime(x) for x in dest_files]
        return dest_files
    # return source file if 
    return [filename]
   
# def hasReadGroup(bamfile):
#     '''Check if a bamfile has read group information, if not we will have to add 
#     @RG tag to the bam file. Note that technically speaking, read group contains
#     flowcell and lane information and should be lane-specific. That is to say a
#     bam file for the same sample might have several @RG with the same SM (sample
#     name) value. However, because GATK only uses SM to identify samples (treats 
#     different RG with the same SM as the same sample), it is OK to add a single 
#     RG tag to a SAM/BAM file is it contains reads for the same sample.'''
#     # the following code consulted a addReadGroups2BAMs.py file available online
#     sam_handle = pysam.Samfile(bamfile, 'r')
#     sam_line = sam_handle.next()
#     read_info = sam_line.qname
#     sam_handle.close()
#     instrument, run_id, flowcell_id, lane = read_info.split(":")[:4]
#     info = {#'sample_name': sample_name,
#             #'locality': locality,
#             #'inline_tag': inline_tag,
#             #'third_read_tag': third_read_tag,
#             'instrument':instrument,  
#             'run_id': run_id,
#             'flowcell_id': flowcell_id,
#             'lane': lane}
#     return info


#  Variant Caller
#
class BaseVariantCaller:
    '''A vase variant caller that is supposed to provide most of the utility functions
    and common operations that are not specific to any pipeline.
    '''
    def __init__(self, resource_dir, pipeline):
        self.resource_dir = os.path.join(os.path.expanduser(resource_dir), pipeline)
        if not os.path.isdir(self.resource_dir):
            env.logger.info('Creating resource directory {}'.format(self.resource_dir))
            os.makedirs(self.resource_dir)

    #
    # PREPARE RESOURCE
    #
    def downloadGATKResourceBundle(self, URL, files):
        '''Utility function to download GATK resource bundle. If files specified by
        files already exist, use the existing downloaded files.'''
        #
        if all([os.path.isfile(x) for x in files]):
            env.logger.warning('Using existing GATK resource')
        else:
            run_command('wget -r {}'.format(URL))
            # walk into the directory and get everything to the top directory
            # this is because wget -r saves files under URL/file
            for root, dirs, files in os.walk('.'):
                for name in files:
                    shutil.move(os.path.join(root, name), name)
        #
        # decompress all .gz files
        for gzipped_file in [x for x in os.listdir('.') if x.endswith('.gz') and 
            not x.endswith('tar.gz')]:
            if existAndNewerThan(ofiles=gzipped_file[:-3], ifiles=gzipped_file):
                env.logger.warning('Using existing decompressed file {}'
                    .format(gzipped_file[:-3]))
            else:
                decompress(gzipped_file, '.')

    def buildBWARefIndex(self, ref_file):
        '''Create BWA index for reference genome file'''
        # bwa index  -a bwtsw wg.fa
        if os.path.isfile(self.REF_fasta + '.amb'):
            env.logger.warning('Using existing bwa indexed sequence {}.amb'.format(self.REF_fasta))
        else:
            checkCmd('bwa')
            run_command('bwa index {}  -a bwtsw {}'
                .format(env.options['OPT_BWA_INDEX'], ref_file))

    def buildSamToolsRefIndex(self, ref_file):
        '''Create index for reference genome used by samtools'''
        if os.path.isfile('{}.fai'.format(ref_file)):
            env.logger.warning('Using existing samtools sequence index {}.fai'
                .format(ref_file))
        else:
            checkCmd('samtools')
            run_command('samtools faidx {} {}'
                .format(env.options['OPT_SAMTOOLS_FAIDX'], ref_file))

    # interface
    def checkResource(self):
        '''Check if needed resource is available.'''
        pass

    def prepareResourceIfNotExist(self):
        '''Prepare all resources for the pipeline. This is pipeline dependent.'''
        pass

    #
    # align and create bam file
    #
    def getFastqFiles(self, input_files):
        '''Decompress or extract input files to get a list of fastq files'''
        filenames = []
        for filename in input_files:
            if filename.lower().endswith('.bam') or filename.lower().endswith('.sam'):
                filenames.extend(self.bam2fastq(filename))
                continue
            for fastq_file in decompress(filename, env.working_dir):
                try:
                    with open(fastq_file) as fastq:
                        line = fastq.readline()
                        if not line.startswith('@'):
                            raise ValueError('Wrong FASTA file {}'.foramt(fastq_file))
                    filenames.append(fastq_file)
                except Exception as e:
                    env.logger.error('Ignoring non-fastq file {}: {}'
                        .format(fastq_file, e))
        filenames.sort()
        return filenames

    def getReadGroup(self, fastq_filename, output_bam):
        '''Get read group information from names of fastq files.'''
        # Extract read group information from filename such as
        # GERALD_18-09-2011_p-illumina.8_s_8_1_sequence.txt. The files are named 
        # according to the lane that produced them and whether they
        # are paired or not: Single-end reads s_1_sequence.txt for lane 1;
        # s_2_sequence.txt for lane 2 Paired-end reads s_1_1_sequence.txt 
        # for lane 1, pair 1; s_1_2_sequence.txt for lane 1, pair 2
        #
        # This function return a read group string like '@RG\tID:foo\tSM:bar'
        #
        # ID* Read group identifier. Each @RG line must have a unique ID. The
        # value of ID is used in the RG
        #     tags of alignment records. Must be unique among all read groups
        #     in header section. Read group
        #     IDs may be modifid when merging SAM fies in order to handle collisions.
        # CN Name of sequencing center producing the read.
        # DS Description.
        # DT Date the run was produced (ISO8601 date or date/time).
        # FO Flow order. The array of nucleotide bases that correspond to the
        #     nucleotides used for each
        #     flow of each read. Multi-base flows are encoded in IUPAC format, 
        #     and non-nucleotide flows by
        #     various other characters. Format: /\*|[ACMGRSVTWYHKDBN]+/
        # KS The array of nucleotide bases that correspond to the key sequence
        #     of each read.
        # LB Library.
        # PG Programs used for processing the read group.
        # PI Predicted median insert size.
        # PL Platform/technology used to produce the reads. Valid values: 
        #     CAPILLARY, LS454, ILLUMINA,
        #     SOLID, HELICOS, IONTORRENT and PACBIO.
        # PU Platform unit (e.g. flowcell-barcode.lane for Illumina or slide for
        #     SOLiD). Unique identifier.
        # SM Sample. Use pool name where a pool is being sequenced.
        #
        filename = os.path.basename(fastq_filename)
        output = os.path.basename(output_bam)
        # sample name is obtained from output filename without file extension
        SM = output.split('.', 1)[0]
        # always assume ILLUMINA for this script and BWA for processing
        PL = 'ILLUMINA'  
        PG = 'BWA'
        #
        # PU is for flowcell and lane information, ID should be unique for each
        #     readgroup
        # ID is temporarily obtained from input filename without exteion
        ID = filename.split('.')[0]
        # try to get lan information from s_x_1/2 pattern
        try:
            PU = re.search('s_([^_]+)_', filename).group(1)
        except AttributeError:
            env.logger.warning('Failed to guess lane information from filename {}'
                .format(filename))
            PU = 'NA'
        # try to get some better ID
        try:
            # match GERALD_18-09-2011_p-illumina.8_s_8_1_sequence.txt
            m = re.match('([^_]+)_([^_]+)_([^_]+)_s_([^_]+)_([^_]+)_sequence.txt', filename)
            ID = '{}.{}'.format(m.group(1), m.group(4))
        except AttributeError as e:
            env.logger.warning('Input fasta filename {} does not match a known'
                ' pattern. ID is directly obtained from filename.'.format(filename))
        #
        rg = r'@RG\tID:{}\tPG:{}\tPL:{}\tPU:{}\tSM:{}'.format(ID, PG, PL, PU, SM)
        env.logger.info('Setting read group tag to {}'.format(rg))
        return rg

    def bwa_aln(self, fastq_files):
        '''Use bwa aln to process fastq files'''
        for input_file in fastq_files:
            dest_file = '{}/{}.sai'.format(env.working_dir, os.path.basename(input_file))
            if existAndNewerThan(ofiles=dest_file, ifiles=input_file):
                env.logger.warning('Using existing alignment index file {}'
                    .format(dest_file))
            else:
                # input file should be in fastq format (-t 4 means 4 threads)
                opt = ' -I ' if fastqVersion(input_file) == 'Illumina 1.3+' else ''
                if opt == ' -I ':
                    env.logger.warning('Using -I option for bwa aln command '
                        'because the sequences seem to be in Illumina 1.3+ format.')
                run_command('bwa aln {} {} -t 4 {}/{} {} > {}_tmp'
                    .format(opt, env.options['OPT_BWA_ALN'], self.resource_dir, 
                        self.REF_fasta, input_file, dest_file),
                    name=os.path.basename(dest_file),
                    upon_succ=(os.rename, dest_file + '_tmp', dest_file),
                    wait=False)
        # wait for all bwa aln jobs to be completed
        wait_all()

    def bwa_sampe(self, fastq_files):
        '''Use bwa sampe to generate aligned sam files for paird end reads'''
        sam_files = []
        for idx in range(len(fastq_files)//2):
            f1 = fastq_files[2*idx]
            f2 = fastq_files[2*idx + 1]
            rg = self.getReadGroup(f1, env.working_dir)
            sai1 = '{}/{}.sai'.format(env.working_dir, os.path.basename(f1))
            sai2 = '{}/{}.sai'.format(env.working_dir, os.path.basename(f2))
            sam_file = '{}/{}_bwa.sam'.format(env.working_dir, os.path.basename(f1))
            if existAndNewerThan(ofiles=sam_file, ifiles=[f1, f2, sai1, sai2]):
                env.logger.warning('Using existing sam file {}'.format(sam_file))
            else:
                run_command(
                    'bwa sampe {0} -r \'{1}\' {2}/{3} {4} {5} {6} {7} > {8}_tmp'
                    .format(
                        env.options['OPT_BWA_SAMPE'], rg, self.resource_dir, self.REF_fasta,
                        sai1, sai2, f1, f2, sam_file),
                    name=os.path.basename(sam_file),
                    upon_succ=(os.rename, sam_file + '_tmp', sam_file),
                    wait=False)
            sam_files.append(sam_file)
        # wait for all jobs to be completed
        wait_all()
        return sam_files

    def bwa_samse(self, fastq_files):
        '''Use bwa sampe to generate aligned sam files'''
        sam_files = []
        for f in fastq_files:
            sam_file = '{}/{}_bwa.sam'.format(env.working_dir, os.path.basename(f))
            rg = self.getReadGroup(f, env.working_dir)
            sai = '{}/{}.sai'.format(env.working_dir, os.path.basename(f))
            if existAndNewerThan(ofiles=sam_file, ifiles=[f, sai]):
                env.logger.warning('Using existing sam file {}'.format(sam_file))
            else:
                run_command(
                    'bwa samse {0} -r \'{1}\' {2}/{3} {4} {6} > {7}_tmp'
                    .format(
                        env.options['OPT_BWA_SAMSE'], rg, self.resource_dir,
                        self.REF_fasta, sai, f, sam_file),
                    name=os.path.basename(sam_file),
                    upon_succ=(os.rename, sam_file + '_tmp', sam_file),
                    wait=False)
            sam_files.append(sam_file)
        # wait for all jobs to be completed
        wait_all()
        return sam_files        

    def countUnmappedReads(self, sam_files):
        #
        # count total reads and unmapped reads
        #
        # The previous implementation uses grep and wc -l, but
        # I cannot understand why these commands are so slow...
        #
        targets = ['{}.counts'.format(x) for x in sam_files]
        if not existAndNewerThan(ofiles=targets, ifiles=sam_files):
            for sam_file, target_file in zip(sam_files, targets):
                env.logger.info('Counting unmapped reads in {}'.format(sam_file))
                unmapped_count = 0
                with open(sam_file) as sam:
                   for idx,line in enumerate(sam):
                       if 'XT:A:N' in line:
                           unmapped_count += 1
                with open(target_file, 'w') as target:
                    target.write('{}\n{}\n'.format(unmapped_count, idx+1))
        #
        counts = []
        for count_file in targets:
            with open(count_file) as cnt:
                unmapped = int(cnt.readline())
                total = int(cnt.readline())
                counts.append((unmapped, total))
        return counts


    def SortSamWithSamtools(self, sam_files):
        '''Convert sam file to sorted bam files.'''
        bam_files = []
        for sam_file in sam_files:
            bam_file = sam_file[:-4] + '.bam'
            if existAndNewerThan(ofiles=bam_file, ifiles=sam_file):
                env.logger.warning('Using existing bam file {}'.format(bam_file))
            else:
                run_command('samtools view {} -bt {}/{}.fai {} > {}'
                    .format(
                        env.options['OPT_SAMTOOLS_VIEW'], self.resource_dir,
                        self.REF_fasta, sam_file, TEMP(bam_file)),
                    name=os.path.basename(bam_file),
                    upon_succ=(os.rename, TEMP(bam_file), bam_file),
                    wait=False)
            bam_files.append(bam_file)
        # wait for all sam->bam jobs to be completed
        wait_all()
        #
        # sort bam files
        sorted_bam_files = []
        for bam_file in bam_files:
            sorted_bam_file = bam_file[:-4] + '_sorted.bam'
            if existAndNewerThan(ofiles=sorted_bam_file, ifiles=bam_file):
                env.logger.warning('Using existing sorted bam file {}'
                    .format(sorted_bam_file))
            else:
                run_command('samtools sort {} {} {}'
                    .format(
                        env.options['OPT_SAMTOOLS_SORT'], bam_file,
                        TEMP(sorted_bam_file)),
                    name=os.path.basename(sorted_bam_file),
                    upon_succ=(os.rename, TEMP(sorted_bam_file), sorted_bam_file),
                    wait=False)
            sorted_bam_files.append(sorted_bam_file)
        wait_all()
        return sorted_bam_files

    def SortSam(self, sam_files, output=None):
        '''Convert sam file to sorted bam files using Picard.'''
        # sort bam files
        sorted_bam_files = []
        for sam_file in sam_files:
            sorted_bam_file = sam_file[:-4] + '_sorted.bam'
            if existAndNewerThan(ofiles=sorted_bam_file, ifiles=sam_file):
                env.logger.warning('Using existing sorted bam file {}'
                    .format(sorted_bam_file))
            else:
                run_command(
                    'java {0} -jar {1}/SortSam.jar {2} I={3} O={4} SO=coordinate'
                    .format(
                        env.options['OPT_JAVA'], env.options['PICARD_PATH'], 
                        env.options['OPT_PICARD_SORTSAM'], sam_file,
                        TEMP(sorted_bam_file)),
                    name=os.path.basename(sorted_bam_file),
                    upon_succ=(os.rename, TEMP(sorted_bam_file), sorted_bam_file),
                    wait=False)
            sorted_bam_files.append(sorted_bam_file)
        wait_all()
        return sorted_bam_files


    def markDuplicates(self, bam_files):
        '''Mark duplicate using picard'''
        dedup_bam_files = []
        for bam_file in bam_files:
            dedup_bam_file = os.path.join(env.working_dir, os.path.basename(bam_file)[:-4] + '.dedup.bam')
            metrics_file = os.path.join(env.working_dir, os.path.basename(bam_file)[:-4] + '.metrics')
            if existAndNewerThan(ofiles=dedup_bam_file, ifiles=bam_file):
                env.logger.warning(
                    'Using existing bam files after marking duplicate {}'
                    .format(dedup_bam_file))
            else:
                run_command('''java {0} -jar {1}/MarkDuplicates.jar {2}
                    INPUT={3}
                    OUTPUT={4}
                    METRICS_FILE={5}
                    ASSUME_SORTED=true
                    VALIDATION_STRINGENCY=LENIENT
                    '''.format(
                        env.options['OPT_JAVA'], env.options['PICARD_PATH'],
                        env.options['OPT_PICARD_MARKDUPLICATES'], bam_file,
                        TEMP(dedup_bam_file),
                        metrics_file), 
                    name=os.path.basename(dedup_bam_file),
                    upon_succ=(os.rename, TEMP(dedup_bam_file), dedup_bam_file),
                    wait=False)
            dedup_bam_files.append(dedup_bam_file)
        wait_all()
        return dedup_bam_files

    def mergeBAMs(self, bam_files):
        '''merge sam files'''
        # use Picard merge, not samtools merge: 
        # Picard keeps RG information from all Bam files, whereas samtools uses only 
        # inf from the first bam file
        merged_bam_file = bam_files[0][:-4] + '_merged.bam'
        if existAndNewerThan(ofiles=merged_bam_file, ifiles=bam_files):
            env.logger.warning('Using existing merged bam file {}'
                .format(merged_bam_file))
        else:
            run_command('''java {} -jar {}/MergeSamFiles.jar {} {}
                USE_THREADING=true
                VALIDATION_STRINGENCY=LENIENT ASSUME_SORTED=true
                OUTPUT={}'''.format(
                    env.options['OPT_JAVA'], env.options['PICARD_PATH'],
                    env.options['OPT_PICARD_MERGESAMFILES'],
                    ' '.join(['INPUT={}'.format(x) for x in bam_files]),
                    TEMP(merged_bam_file)),
                name=os.path.basename(merged_bam_file),
                upon_succ=(os.rename, TEMP(merged_bam_file), merged_bam_file))
        return merged_bam_file

    def indexBAM(self, bam_file):
        '''Index the input bam file'''
        if existAndNewerThan(ofiles='{}.bai'.format(bam_file), ifiles=bam_file):
            env.logger.warning('Using existing bam index {}.bai'.format(bam_file))
        else:
            run_command('samtools index {0} {1} {1}_tmp.bai'.format(
                env.options['OPT_SAMTOOLS_INDEX'], bam_file),
                upon_succ=(os.rename, bam_file + '_tmp.bai', bam_file + '.bai'))

    def align(self, input_files, output):
        '''Align to the reference genome'''
        if not output.endswith('.bam'):
            env.logger.error('Plase specify a .bam file in the --output parameter')
            sys.exit(1)

    def realignIndels(self, bam_file, knownSites):
        '''Create realigner target and realign indels'''
        target = os.path.join(env.working_dir, os.path.basename(bam_file)[:-4] + '.IndelRealignerTarget.intervals')
        if existAndNewerThan(ofiles=target, ifiles=bam_file):
            env.logger.warning('Using existing realigner target {}'.format(target))
        else:
            run_command('''java {0} -jar {1}/GenomeAnalysisTK.jar {2} -I {3} 
                -R {4}/{5}
                -T RealignerTargetCreator
                --mismatchFraction 0.0
                -o {6}_tmp {7} '''.format(
                    env.options['OPT_JAVA'], env.options['GATK_PATH'],
                    env.options['OPT_GATK_REALIGNERTARGETCREATOR'],
                    bam_file, self.resource_dir,
                    self.REF_fasta, target,
                    ' '.join(['-known {}/{}'.format(self.resource_dir, x) for x in knownSites])),
                name=os.path.basename(target),
                upon_succ=(os.rename, target + '_tmp', target))
        # 
        # realign around known indels
        cleaned_bam_file = os.path.join(env.working_dir, os.path.basename(bam_file)[:-4] + '.clean.bam')
        if existAndNewerThan(ofiles=cleaned_bam_file, ifiles=target):
            env.logger.warning('Using existing realigner bam file {}'.format(cleaned_bam_file))
        else:
            run_command('''java {0} -jar {1}/GenomeAnalysisTK.jar {2} -I {3} 
                -R {4}/{5}
                -T IndelRealigner 
                --targetIntervals {6}
                --consensusDeterminationModel USE_READS
                -compress 0 -o {7} {8}'''.format(
                    env.options['OPT_JAVA'], env.options['GATK_PATH'],
                    env.options['OPT_GATK_REALIGNERTARGETCREATOR'],
                    bam_file, self.resource_dir,
                    self.REF_fasta, target, TEMP(cleaned_bam_file),
                    ' '.join(['-known {}/{}'.format(self.resource_dir, x) for x in knownSites])),
                name=os.path.basename(cleaned_bam_file),
                upon_succ=(os.rename, TEMP(cleaned_bam_file), cleaned_bam_file))
        # 
        return cleaned_bam_file


    def recalibrate(self, bam_file, recal_bam_file, knownSites):
        '''Create realigner target and realign indels'''
        target = os.path.join(env.working_dir, os.path.basename(bam_file)[:-4] + '.grp')
        if existAndNewerThan(ofiles=target, ifiles=bam_file):
            env.logger.warning('Using existing base recalibrator target {}'.format(target))
        else:
            run_command('''java {0} -jar {1}/GenomeAnalysisTK.jar {2} 
                -T BaseRecalibrator
                -I {3} 
                -R {4}/{5}
                -cov ReadGroupCovariate
                -cov QualityScoreCovariate
                -cov CycleCovariate
                -cov ContextCovariate
                -o {6}_tmp {7}'''.format(
                    env.options['OPT_JAVA'], env.options['GATK_PATH'],
                    env.options['OPT_GATK_BASERECALIBRATOR'], bam_file,
                    self.resource_dir, self.REF_fasta, target,
                    ' '.join(['-knownSites {}/{}'.format(self.resource_dir, x) for x in knownSites])),
                name=os.path.basename(target),
                upon_succ=(os.rename, target + '_tmp', target))
        #
        # recalibrate
        if existAndNewerThan(ofiles=recal_bam_file, ifiles=target):
            env.logger.warning('Using existing recalibrated bam file {}'.format(recal_bam_file))
        else:
            run_command('''java {0} -jar {1}/GenomeAnalysisTK.jar {2}
                -T PrintReads
                -I {3} 
                -R {4}/{5}
                -BQSR {6}
                -o {7}'''.format(
                    env.options['OPT_JAVA'], env.options['GATK_PATH'],
                    env.options['OPT_GATK_PRINTREADS'], bam_file,
                    self.resource_dir, self.REF_fasta, target, 
                    TEMP(recal_bam_file)),
                name=os.path.basename(recal_bam_file),
                upon_succ=(os.rename, TEMP(recal_bam_file), recal_bam_file))
        # 
        return recal_bam_file

    def reduceReads(self, input_file):
        target = input_file[:-4] + '_reduced.bam'
        if existAndNewerThan(ofiles=target, ifiles=input_file):
            env.logger.warning('Using existing reduced bam file {}'.format(target))
        else:
            run_command('''java {0} -jar {1}/GenomeAnalysisTK.jar {2}
                -T ReduceReads
                -I {3} 
                -R {4}/{5}
                -o {6}'''.format(
                    env.options['OPT_JAVA'], env.options['GATK_PATH'],
                    env.options['OPT_GATK_REDUCEREADS'], input_file,
                    self.resource_dir, self.REF_fasta,
                    TEMP(target)),
                name=os.path.basename(target),
                upon_succ=(os.rename, TEMP(target), target))
        # 
        return target

    def unifiedGenotyper(self, input_file):
        target = os.path.join(env.working_dir,
            os.path.basename(input_file)[:-4] + '.vcf')
        if existAndNewerThan(ofiles=target, ifiles=input_file):
            env.logger.warning('Using existing called variants {}'.format(target))
        else:
            dbSNP_vcf = [x for x in self.knownSites if 'dbsnp' in x][0]
            run_command('''java {0} -jar {1}/GenomeAnalysisTK.jar {2} -I {3} 
                -R {4}/{5}
                -T UnifiedGenotyper
                --dbsnp {4}/{7}
                -stand_call_conf 50.0 
                -stand_emit_conf 10.0 
                -dcov 200
                -o {6}'''.format(
                    env.options['OPT_JAVA'], env.options['GATK_PATH'],
                    env.options['OPT_GATK_UNIFIEDGENOTYPER'], input_file,
                    self.resource_dir, self.REF_fasta,
                    TEMP(target), dbSNP_vcf),
                name=os.path.basename(target),
                upon_succ=(os.rename, TEMP(target), target))
        # 
        return target

    def haplotypeCall(self, input_file):
        target = os.path.join(env.working_dir,
            os.path.basename(input_file)[:-4] + '.vcf')
        if existAndNewerThan(ofiles=target, ifiles=input_file):
            env.logger.warning('Using existing called variants {}'.format(target))
        else:
            run_command('''java {0} -jar {1}/GenomeAnalysisTK.jar {2} -I {3} 
                -R {4}/{5}
                -T HaplotypeCaller
                -minPruning 3
                -o {6}'''.format(
                    env.options['OPT_JAVA'], env.options['GATK_PATH'],
                    env.options['OPT_GATK_HAPLOTYPECALLER'], input_file,
                    self.resource_dir, self.REF_fasta,
                    TEMP(target)),
                name=os.path.basename(target),
                upon_succ=(os.rename, TEMP(target), target))
        # 
        return target


    def variantRecalibration(self, input_file):
        target = os.path.join(env.working_dir,
            os.path.basename(input_file)[:-4] + '_recal.vcf')
        if existAndNewerThan(ofiles=target, ifiles=input_file):
            env.logger.warning('Using existing recalibrated variants {}'.format(target))
        else:
            run_command('''java {0} -jar {1}/GenomeAnalysisTK.jar {2} -I {3} 
                -R {4}/{5}
                -T VariantRecalibrator
                -resource:hapmap,known=false,training=true,truth=true,prior=15.0 hapmap_3.3.b37.sites.vcf		
                -resource:omni,known=false,training=true,truth=false,prior=12.0	1000G_omni2.5.b37.sites.vcf		
                -resource:dbsnp,known=true,training=false,truth=false,prior=6.0	dbsnp_137.b37.v	
                -an	QD	-an	MQ	-an	HaplotypeScore {}
                -mode SNP 
                -recalFile raw.SNPs.recal
                -tranchesFile raw.SNPs.tranches
                -rscriptFile recal.plots.R
                -o {6}'''.format(
                    env.options['OPT_JAVA'], env.options['GATK_PATH'],
                    env.options['OPT_GATK_HAPLOTYPECALLER'], input_file,
                    self.resource_dir, self.REF_fasta,
                    TEMP(target)),
                name=os.path.basename(target),
                upon_succ=(os.rename, TEMP(target), target))
        # 
        if existAndNewerThan(ofiles=target, ifiles=input_file):
            env.logger.warning('Using existing recalibrated variants {}'.format(target))
        else:
            run_command('''java {0} -jar {1}/GenomeAnalysisTK.jar {2} -I {3} 
                -R {4}/{5}
                -T ApplyRecalibraBon
                -mode SNP 
                -recalFile raw.SNPs.recal
                -tranchesFile raw.SNPs.trances
                -o {6}'''.format(
                    env.options['OPT_JAVA'], env.options['GATK_PATH'],
                    env.options['OPT_GATK_HAPLOTYPECALLER'], input_file,
                    self.resource_dir, self.REF_fasta,
                    TEMP(target)),
                name=os.path.basename(target),
                upon_succ=(os.rename, TEMP(target), target))
        return target


    def callVariants(self, input_files, ped_file, output):
        '''Call variants from a list of input files'''
        if not output.endswith('.vcf'):
           env.logger.error('Please specify a .vcf file in the --output parameter')
           sys.exit(1)
        for bam_file in input_files:
            if not os.path.isfile(bam_file):
                env.logger.error('Input file {} does not exist'.format(bam_file))
                sys.exit(1)
            if not os.path.isfile(bam_file + '.bai'):
                env.logger.error('Input bam file {} is not indexed.'.format(bam_file))
                sys.exit(1)

class b37_gatk_23(BaseVariantCaller):
    '''A variant caller that uses gatk resource package 2.3 to call variants
    from build b37 of the human genome of the GATK resource bundle'''
    def __init__(self, resource_dir):
        self.pipeline = 'b37_gatk_23'
        BaseVariantCaller.__init__(self, resource_dir, self.pipeline)
        self.GATK_resource_url = 'ftp://gsapubftp-anonymous@ftp.broadinstitute.org/bundle/2.3/b37/*'
        self.REF_fasta = 'human_g1k_v37.fasta'
        self.knownSites = [
            'dbsnp_137.b37.vcf',
            'hapmap_3.3.b37.vcf',
            '1000G_omni2.5.b37.vcf',
            'Mills_and_1000G_gold_standard.indels.b37.vcf',
            '1000G_phase1.indels.b37.vcf',
        ]


    def checkResource(self):
        '''Check if needed resource is available. This pipeline requires
        GATK resource bundle, commands wget, bwa, samtools, picard, and GATK. '''
        saved_dir = os.getcwd()
        os.chdir(self.resource_dir)
        files = [self.REF_fasta, self.REF_fasta + '.amb', self.REF_fasta + '.fai']
        if not all([os.path.isfile(x) for x in files]):
            sys.exit('GATK resource bundle does not exist in directory {}. '
                'Please run "call_variants.py prepare_resource" befor you '
                'execute this command.'.format(self.resource_dir))
        #
        for cmd in ['wget',     # to download resource
                    'bwa',      # alignment
                    'samtools'  # merge sam files
                    ]:
            checkCmd(cmd)
        #
        checkPicard()
        checkGATK()
        # checkPySam()
        #
        os.chdir(saved_dir)

    def prepareResourceIfNotExist(self):
        '''This function downloads the UCSC resource boundle for specified build and
        creates bwa and samtools indexed files from the whole genome sequence '''
        saved_dir = os.getcwd()
        os.chdir(self.resource_dir)
        #
        # these are pipeline specific
        self.downloadGATKResourceBundle(self.GATK_resource_url, files=[self.REF_fasta + '.gz'])
        self.buildBWARefIndex(self.REF_fasta)
        self.buildSamToolsRefIndex(self.REF_fasta)
        # 
        os.chdir(saved_dir)

    def bam2fastq(self, input_file):
        '''This function extracts raw reads from an input BAM file to one or 
        more fastq files.'''
        #
        # check if the bam file is paired or not (FIXME)
        output_files = [os.path.join(env.working_dir, '{}_{}.fastq'
            .format(os.path.basename(input_file)[:-4], x)) for x in [1,2]]
        if all([os.path.isfile(x) for x in output_files]):
            env.logger.warning('Using existing sequence files {}'
                .format(' and '.join(output_files)))
        else:
            run_command('''java {} -jar {}/SamToFastq.jar INPUT={}
                FASTQ={}_tmp SECOND_END_FASTQ={} NON_PF=true'''
                .format(env.options['OPT_JAVA'],
                    env.options['PICARD_PATH'], input_file,
                    output_files[0], output_files[1]),
                name=os.path.basename(output_files[0]),
                upon_succ=(os.rename, output_files[0] + '_tmp', output_files[0]))
        return output_files


    def align(self, input_files, output):
        '''Align reads to hg19 reference genome and return a sorted, indexed bam file.'''
        BaseVariantCaller.align(self, input_files, output)
        #
        # the working dir is a directory under output, the middle files are saved to this
        # directory to avoid name conflict
        env.working_dir = os.path.join(os.path.split(output)[0], os.path.basename(output) + '_align_cache')
        #
        # step 1: decompress to get a list of fastq files
        fastq_files = self.getFastqFiles(input_files)
        #
        # step 2: call bwa aln to produce .sai files
        self.bwa_aln(fastq_files)
        #
        # step 3: generate .sam files for each pair of pairend reads, or reach file of unpaired reads
        paired = True
        if len(fastq_files) // 2 * 2 != len(fastq_files):
            env.logger.warning('Odd number of fastq files found, not handled as paired end reads.')
            paired = False
        for idx in range(len(fastq_files)//2):
            f1 = fastq_files[2*idx]
            f2 = fastq_files[2*idx + 1]
            if len(f1) != len(f2):
                env.logger.warning(
                    'Filenames {}, {} are not paired, not handled as paired end reads.'
                    .format(f1, f2))
                paired = False
                break
            diff = [ord(y)-ord(x) for x,y in zip(f1, f2) if x!=y]
            if diff != [1]:
                env.logger.warning(
                    'Filenames {}, {} are not paired, not handled as paired end reads.'
                    .format(f1, f2))
                paired = False
                break
        #
        # sam files?
        if paired:
            sam_files = self.bwa_sampe(fastq_files)
        else:
            sam_files = self.bwa_samse(fastq_files)
        #
        counts = self.countUnmappedReads(sam_files)
        for f,c in zip(sam_files, counts):
            # more than 40% unmapped
            if c[0]/c[1] > 0.4:
                env.logger.error('{}: {} out of {} reads are unmapped ({:.2f}% mapped)'
                    .format(f, c[0], c[1], 100*(1 - c[0]/c[1])))
                sys.exit(1)
            else:
                env.logger.info('{}: {} out of {} reads are unmapped ({:.2f}% mapped)'
                    .format(f, c[0], c[1], 100*(1 - c[0]/c[1])))
        # 
        # step 4: convert sam to sorted bam files
        sorted_bam_files = self.SortSam(sam_files)
        #
        # According to GATK best practice, dedup should be run at the
        # lane level (the documentation is confusing here though)
        #
        # step 5: remove duplicate
        dedup_bam_files = self.markDuplicates(sorted_bam_files)
        #
        # step 6: merge sorted bam files to output file
        if len(dedup_bam_files) > 1:
            merged_bam_file = self.mergeBAMs(dedup_bam_files)
        else:
            merged_bam_file = dedup_bam_files[0]
        #
        # step 7: index the output bam file
        self.indexBAM(merged_bam_file)
        #
        # step 7: create indel realignment targets and recall
        cleaned_bam_file = self.realignIndels(merged_bam_file, knownSites=self.knownSites)
        self.indexBAM(cleaned_bam_file)
        #
        # step 8: recalibration
        self.recalibrate(cleaned_bam_file, output, knownSites=self.knownSites)
        self.indexBAM(output)
        #
        # step 9: reduce reads
        reduced = self.reduceReads(output)
        self.indexBAM(reduced)

    def callVariants(self, input_files, pedfile, output):
        '''Call variants from a list of input files'''
        BaseVariantCaller.callVariants(self, input_files, pedfile, output)
        env.working_dir = os.path.join(os.path.split(output)[0], os.path.basename(output) + '_call_cache')
        #
        # step 1: haplotype call
        for input_file in input_files:
            #vcf_file = self.haplotypeCall(input_file)
            vcf_file = self.unifiedGenotyper(input_file)
        #


class hg19_gatk_23(b37_gatk_23):
    '''A variant caller that uses gatk resource package 2.3 to call variants
    from build hg19 of the human genome'''
    def __init__(self, resource_dir):
        self.pipeline = 'hg19_gatk_23'
        b37_gatk_23.__init__(self, resource_dir, self.pipeline)
        #
        # this piple just uses different resource bundle
        self.GATK_resource_url = 'ftp://gsapubftp-anonymous@ftp.broadinstitute.org/bundle/2.3/hg19/*'
        self.REF_fasta = 'ucsc.hg19.fasta'
        self.knownSites = [
            'dbsnp_137.hg19.vcf',
            'hapmap_3.3.hg19.vcf',
            '1000G_omni2.5.hg19.vcf',
            'Mills_and_1000G_gold_standard.indels.hg19.vcf',
            '1000G_phase1.indels.hg19.vcf',
        ]
        

if __name__ == '__main__':
    #
    options = [
        ('PICARD_PATH', ''),
        ('GATK_PATH', ''),
        ('OPT_JAVA', '-Xmx4g -XX:-UseGCOverheadLimit'),
        ('OPT_BWA_INDEX', ''),
        ('OPT_SAMTOOLS_FAIDX', ''),
        ('OPT_BWA_ALN', ''),
        ('OPT_BWA_SAMPE', ''),
        ('OPT_BWA_SAMSE', ''),
        ('OPT_SAMTOOLS_VIEW', ''),
        ('OPT_SAMTOOLS_SORT', ''),
        ('OPT_SAMTOOLS_INDEX', ''),
        ('OPT_PICARD_MERGESAMFILES', 'MAX_RECORDS_IN_RAM=5000000'),
        # validation_stringency=leniant is used to correct an error
        # caused by some versions of BWA, see
        #
        #   http://seqanswers.com/forums/showthread.php?t=4246
        #
        # for details
        ('OPT_PICARD_SORTSAM', 'VALIDATION_STRINGENCY=LENIENT'),
        ('OPT_GATK_REALIGNERTARGETCREATOR', ''),
        ('OPT_GATK_INDELREALIGNER', ''),
        ('OPT_PICARD_MARKDUPLICATES', ''),
        ('OPT_GATK_BASERECALIBRATOR', '-rf BadCigar'),
        ('OPT_GATK_PRINTREADS', ''),
        ('OPT_GATK_REDUCEREADS', ''),
        ('OPT_GATK_HAPLOTYPECALLER', ''),
        ('OPT_GATK_UNIFIEDGENOTYPER', '-rf BadCigar'),
        ]
    def addCommonArguments(parser, args):
        if 'pipeline' in args:
            parser.add_argument('--pipeline', nargs='?', default='b37_gatk_23',
                choices=['hg19_gatk_23', 'b37_gatk_23'],
                help='Name of the pipeline to be used to call variants.')
        if 'resource_dir' in args:
            parser.add_argument('--resource_dir', default='~/.variant_tools/var_caller', 
                help='''A directory for resources used by variant caller. Default to
                    ~/.variant_tools/var_caller.''')
        if 'set' in args:
            parser.add_argument('--set', nargs='*',
                help='''Set runtime variables in the format of NAME=value. NAME can be
                    PICARD_PATH (path to picard, should have a number of .jar files 
                    under it), GATK_PATH (path to gatk, should have GenomeAnalysisTK.jar
                    under it) for path to JAR files (can be ignored if the paths are
                    specified in environment variable $CLASSPATH), additional options
                    to command java (OPT_JAVA, parameter to the java command, default
                    to value "-Xmx4g"), and options to individual subcommands such as
                    OPT_BWA_INDEX (additional option to bwa index) and OPT_SAMTOOLS_FAIDX.
                    The following options are acceptable: {}'''.format(', '.join(
                    [x[0] + (' (default: {})'.format(x[1]) if x[1] else '') for x in options])))
        if 'jobs' in args:
            parser.add_argument('-j', '--jobs', default=1, type=int,
                help='''Maximum number of concurrent jobs.''')
    #
    master_parser = argparse.ArgumentParser(description='''Pipelines to call variants
        from raw sequence files, or single-sample bam files. It works (tested) only
        for Illumina sequence data, and for human genome with build hg19 of the
        reference genome. This pipeline uses BWA for alignment and GATK for variant
        calling, and Picard for various other options..''')

    subparsers = master_parser.add_subparsers(title='Available operations', dest='action')
    #
    # action prepare_resource
    #
    resource = subparsers.add_parser('prepare_resource',
        help='Prepare resources for subsequent variant calling operations.',
        description='''This operation downloads GATK resource bundle and creates
            indexed reference genomes to be used by other tools.''')
    addCommonArguments(resource, ['pipeline', 'resource_dir'])
    #
    # action align
    #
    align = subparsers.add_parser('align',
        help='''Align raw reads to reference genome and return a compressed BAM file.
            The input files should be reads for the same sample, which could be individual
            fastq files, a tar file with all fastq files, or their gziped or bzipped
            versions. Filenames ending with _1 _2 will be considered as paired end reads.''')
    align.add_argument('input_files', nargs='+',
        help='''One or more .txt, .fa, .fastq, .tar, .tar.gz, .tar.bz2, .tbz2, .tgz files
            that contain raw reads of a single sample. Files in sam/bam format are also
            acceptable, in which case raw reads will be extracted and aligned again to 
            generate a new bam file. ''')
    align.add_argument('-o', '--output', required=True,
        help='''Output aligned reads to a sorted, indexed, dedupped, and recalibrated
            BAM file $output.bam.''')
    addCommonArguments(align, ['pipeline', 'resource_dir', 'set', 'jobs'])
    #
    # action call
    #
    call = subparsers.add_parser('call',
        help='''Call variants from a list of calibrated BAM files.''')
    call.add_argument('input_files', nargs='+',
        help='''One or more BAM files.''')
    call.add_argument('-o', '--output', required=True,
        help='''Output called variants to the specified VCF file''')
    call.add_argument('--pedfile',
        help='''A pedigree file that specifies the relationship between input
            samples, used for multi-sample calling.''')
    addCommonArguments(call, ['pipeline', 'resource_dir', 'set', 'jobs'])
    #
    args = master_parser.parse_args()
    #
    if hasattr(args, 'output'):
        if type(args.output) == list:
            env.working_dir = os.path.split(args.output[0])[0]
            logname = os.path.basename(args.output[0]) + '.log'
        else:
            env.working_dir = os.path.split(args.output)[0]
            logname = os.path.basename(args.output) + '.log'
        # screen + log file logging
        env.logger = os.path.join(env.working_dir, logname)
    else:
        # screen only logging
        env.logger = None
    #
    # handling additional parameters
    # set default value
    for opt in options:
        if opt[1]:
            env.options[opt[0]] = opt[1]
    # override using command line values
    if hasattr(args, 'set') and args.set is not None:
        for arg in args.set:
            if '=' not in arg:
                sys.exit('Additional parameter should have form NAME=value')
            name, value = arg.split('=', 1)
            if name not in [x[0] for x in options]:
                env.logger.error('Unrecognized environment variable {}: {} are allowed.'.format(
                    name, ', '.join([x[0] for x in options])))
                sys.exit(1)
            env.options[name] = value
            env.logger.info('Environment variable {} is set to {}'.format(name, value))
    #
    # get a pipeline: args.pipeline is the name of the pipeline, also the name of the
    # class (subclass of VariantCaller) that implements the pipeline
    pipeline = eval(args.pipeline)(args.resource_dir)
    if args.action == 'prepare_resource':
        pipeline.prepareResourceIfNotExist()
    elif args.action == 'align':
        env.jobs = args.jobs
        checkPicard()
        pipeline.checkResource()
        pipeline.align(args.input_files, args.output)
    elif args.action == 'call':
        env.jobs = args.jobs
        checkPicard()
        checkGATK()
        pipeline.checkResource()
        pipeline.callVariants(args.input_files, args.pedfile, args.output)

