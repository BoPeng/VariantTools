#!/usr/bin/env python
#
# $File: association.py $
# $LastChangedDate$
# $Rev$
#
# This file is part of variant_tools, a software application to annotate,
# summarize, and filter variants for next-gen sequencing ananlysis.
# Please visit http://varianttools.sourceforge.net for details.
#
# Copyright (C) 2011 - 2013 Bo Peng (bpeng@mdanderson.org) and Gao Wang (wangow@gmail.com)
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

import sys, os, re
from multiprocessing import Process, Queue, Pipe, Value, Array, Lock
import time
import random
import math
from copy import copy, deepcopy
from .project import Project, Field, AnnoDB, AnnoDBWriter, MaintenanceProcess
from .utils import ProgressBar, consolidateFieldName, DatabaseEngine, delayedAction, \
     env, executeUntilSucceed, ShelfDB, safeMapFloat, PrettyPrinter, flatten, hasGenoInfo
from .phenotype import Sample
from .tester import *
from .rtester import RTest, SKAT

if sys.version_info.major == 2:
    from vt_sqlite3_py2 import OperationalError
else:
    from variant_tools.vt_sqlite3_py3 import OperationalError
import argparse

def associateArguments(parser):
    data = parser.add_argument_group('Genotype, phenotype, and covariates')
    data.add_argument('variants', help='''Table of variants to be tested.''')
    data.add_argument('phenotypes', nargs=1,
        help='''A list of phenotypes that will be passed to the association
            statistics calculator. Currently only a single phenotype is allowed.''')
    data.add_argument('--covariates', nargs='*', default=[],
        help='''Optional phenotypes that will be passed to statistical
            tests as covariates. Values of these phenotypes should be integer
            or float.''')
    data.add_argument('--var_info', nargs='*', default=[],
        help='''Optional variant information fields (e.g. minor allele frequency
            from 1000 genomes project) that will be passed to statistical tests.
            The fields could be any annotation fields of with integer or float
            values, including those from used annotation databases (use "vtools
            show fields" to see a list of usable fields). ''')
    data.add_argument('--geno_info', nargs='*', default=[],
        help='''Optional genotype fields (e.g. quality score of genotype calls,
            cf. "vtools show genotypes") that will be passed to statistical
            tests. Note that the fields should exist for all samples that are
            tested.''')
    data.add_argument('--geno_name', default='GT',
        help='''Field name of genotype, default to 'GT'. If another field name is specified, 
            for example if imputation scores are available as 'DS' (dosage), then
            the given field 'DS' will be used as genotype data for association analysis.''')
    tests = parser.add_argument_group('Association tests')
    tests.add_argument('-m', '--methods', nargs='+',
        help='''Method of one or more association tests. Parameters for each
            method should be specified together as a quoted long argument (e.g.
            --methods "m --alternative 2" "m1 --permute 1000"), although
            the common method parameters can be specified separately, as long as
            they do not conflict with command arguments. (e.g. --methods m1 m2 -p 1000
            is equivalent to --methods "m1 -p 1000" "m2 -p 1000".). You can use
            command 'vtools show tests' for a list of association tests, and
            'vtools show test TST' for details about a test. Customized association
            tests can be specified as mod_name.test_name where mod_name should
            be a Python module (system wide or in the current directory), and
            test_name should be a subclass of NullTest.''')
    tests.add_argument('-g', '--group_by', nargs='*',
        help='''Group variants by fields. If specified, variants will be separated
            into groups and are tested one by one.''')
    filters = parser.add_argument_group('Select and filter samples and genotypes')
    filters.add_argument('-s', '--samples', nargs='*', metavar='COND', default=[],
        help='''Limiting variants from samples that match conditions that
            use columns shown in command 'vtools show sample' (e.g. 'aff=1',
            'filename like "MG%%"'). Each line of the sample table (vtools show
            samples) is considered as samples. If genotype of a physical sample
            is scattered into multiple samples (e.g. imported chromosome by
            chromosome), they should be merged using command vtools admin.''')
    filters.add_argument('--genotypes', nargs='*', metavar='COND', default=[],
        help='''Limiting genotypes to those matching conditions that
            use columns shown in command 'vtools show genotypes' (e.g. 'GQ>15').
            Genotypes failing such conditions will be regarded as missing genotypes.''')
    filters.add_argument('--discard_samples', metavar='EXPR', nargs='*', default=[],
        help='''Discard samples that match specified conditions within each test
            group (defined by parameter --group_by). Currently only expressions in
            the form of "%%(NA)>p" is providedted to remove samples that have more 100*p
            percent of missing values.''')
    filters.add_argument('--discard_variants', metavar='EXPR', nargs='*', default=[],
        help='''Discard variant sites based on specified conditions within each test
            group. Currently only expressions in the form of '%%(NA)>p' is provided to
            remove variant sites that have more than 100*p percent of missing genotypes.
            Note that this filter will be applied after "--discard_samples" is applied,
            if the latter also is specified.''' )
    output = parser.add_argument_group('Output of test statistics')
    output.add_argument('--to_db', metavar='annoDB',
        help='''Name of a database to which results from association tests will
            be written. Groups with existing results in the database will be
            ignored unless parameter --force is used.''')
    output.add_argument('-d', '--delimiter', default=None,
        help='''Delimiter use to separate columns of output. The default output uses
            multiple spaces to align columns of output. Use '-d,' for csv output, or
            -d'\\t' for tab-delimited output.''')
    output.add_argument('-f', '--force', action='store_true',
        help='''Analyze all groups including those that have recorded results in
            the result database.''')
    parser.add_argument('-j', '--jobs', metavar='N', default=1, type=int,
        help='''Number of processes to carry out association tests.''')

class AssociationTestManager:
    '''Parse command line and get data for association testing. This class will provide
    the following attributes for others to use:

    tests:       a list of test objects
    sample_IDs:  sample IDs
    sample_names:   sample names
    table:       variant table (genotype)
    phenotypes:  phenotypes
    covariates:  covariates
    var_info:    variant information fields
    geno_info:   genotype information fields
    groups:      a list of groups
    group_names: names of the group
    '''
    def __init__(self, proj, table, phenotypes, covariates, var_info, geno_info, geno_name, methods,
        unknown_args, samples, genotypes, group_by, discard_samples, discard_variants):
        self.proj = proj
        self.db = proj.db
        self.genotypes = genotypes
        self.phenotype_names = phenotypes
        self.covariate_names = covariates
        # table?
        if not self.proj.isVariantTable(table):
            raise ValueError('Variant table {} does not exist.'.format(table))
        self.table = table
        self.var_info, self.geno_info = self.getInfo(methods, var_info, geno_info)
        self.geno_name = geno_name
        # step 1: get missing filter conditions
        self.missing_ind_ge, self.missing_var_ge = \
          self.getMissingFilters(discard_samples, discard_variants)
        #
        # step 2: get testers
        self.tests = self.getAssoTests(methods, len(covariates), unknown_args)
        self.num_extern_tests = sum([isinstance(x, ExternTest) for x in self.tests])
        #
        # step 3-1: get samples and related phenotypes
        self.sample_IDs, self.sample_names, self.phenotypes, self.covariates = \
          self.getPhenotype(samples, phenotypes, covariates)
        # step 3-2: check if all samples has all genotype info
        has_geno_info = hasGenoInfo(self.proj, self.sample_IDs, self.geno_info)
        if False in has_geno_info:
            env.logger.error("Cannot find genotype information '{0}'".\
                             format(self.geno_info[has_geno_info.index(False)]))
            sys.exit(1)
        # step 4-1: check if tests are compatible with phenotypes
        for idx, item in enumerate([list(set(x)) for x in self.phenotypes]):
            if (list(map(float, item)) == [2.0, 1.0] or list(map(float, item)) == [1.0, 2.0]):
                self.phenotypes[idx] = [i - 1.0 for i in self.phenotypes[idx]]
                item = [i - 1.0 for i in item]
            if not (list(map(float, item)) == [0.0, 1.0] or list(map(float, item)) == [1.0, 0.0]):
                for test in self.tests:
                    if test.trait_type == 'disease':
                        raise ValueError("{0} cannot handle non-binary phenotype value(s) {1}".\
                                         format(test.__class__.__name__, '/'.join([str(x) for x in item])))
        # step 4-2: check if 'SSeq_common' is valid to use
        if 'SSeq_common' in [test.__class__.__name__ for test in self.tests] and group_by:
            raise ValueError("SSeq_common method cannot be used with --group_by/-g")
        #
        #
        # We automatically index genotypes before when we retrieved genotypes for each group.
        # With the new load genotype method, genotypes are loaded in batch so indexing does not
        # help that much. Because creating indexes are slow and take a lot of disk space, we
        # disable auto-indexing for this command. If the user are going to run this command
        # a lot, they can create indexes explicitly using command 'vtools admin --index_genotypes'
        #
        #
        # step 5: indexes genotype tables if needed
        # proj.db.attach('{}_genotype.DB'.format(proj.name), '__fromGeno')
        # unindexed_IDs = []
        # for id in self.sample_IDs:
        #     if not proj.db.hasIndex('__fromGeno.genotype_{}_index'.format(id)):
        #         unindexed_IDs.append(id)
        # if unindexed_IDs:
        #     cur = proj.db.cursor()
        #     prog = ProgressBar('Indexing genotypes', len(unindexed_IDs))
        #     for idx, ID in enumerate(unindexed_IDs):
        #         cur.execute('CREATE INDEX __fromGeno.genotype_{0}_index ON genotype_{0} (variant_id ASC)'.format(ID))
        #         prog.update(idx + 1)
        #     prog.done()
        #
        # step 6: get groups
        self.group_names, self.group_types, self.groups = self.identifyGroups(group_by)

    def getInfo(self, methods, var_info, geno_info):
        '''automatically update var_info or geno_info if input extern weights found in either'''
        for m in methods:
            if not ("--extern_weight" in m or "--extern_weight" in sys.argv):
                continue
            if "--extern_weight" in m:
                target = m
            else:
                target = ' '.join(sys.argv)
            extern_weight = []
            for item in re.match(r'.*?--extern_weight(.*?)-|.*?--extern_weight(.*?)$', target).groups():
                if item is not None:
                    extern_weight.extend(item.strip().split())
            fields = flatten([[x.name for x in db.fields] for db in self.proj.annoDB]) + \
                     flatten([['{}.{}'.format(db.name, x.name) for x in db.fields] for db in self.proj.annoDB])
            fields += [field for field in self.proj.db.getHeaders(self.table) \
                       if field not in ('variant_id', 'bin', 'alt_bin')]
            print(fields)
            for item in extern_weight:
                if not item in geno_info + var_info:
                    # Is extern_weight in var_info?
                    if item in fields:
                        var_info.append(item)
                        env.logger.info('Loading variant / annotation field "{0}" for association analysis.'.\
                                        format(item))
                    else:
                        geno_info.append(item)
                        env.logger.warning('Cannot find external weight "{0}" as a variant or annotation '\
                                         'field. Treating it as genotype information field'.format(item))
        return var_info, geno_info 

    def getMissingFilters(self, discard_samples, discard_variants):
        missing_ind_ge = 1.0
        missing_var_ge = 1.0
        # sample level missingness filter
        for expr in discard_samples:
            try:
                sep = re.search(r'>|=|<|>=|<=', expr)
                if sep is not None:
                    sep = sep.group(0)
                else:
                    raise ValueError
                e, value = [x.strip() for x in expr.split(sep)]
                if e == '%(NA)' and sep == '>':
                    # missing individual level genotypes greater than
                    missing_ind_ge = float(value)
                else:
                    raise ValueError('Invalid expression {}'.format(expr))
            except ValueError:
                raise ValueError('Unrecognized expression {}: '
                                 'currently supported expressions are "%(NA)>NUM".'.format(expr))
        # variant level missingness filter
        for expr in discard_variants:
            try:
                sep = re.search(r'>|=|<|>=|<=', expr)
                if sep is not None:
                    sep = sep.group(0)
                else:
                    raise ValueError
                e, value = [x.strip() for x in expr.split(sep)]
                if e == '%(NA)' and sep == '>':
                    # missing variant level genotypes greater than
                    missing_var_ge = float(value)
                else:
                    raise ValueError('Invalid expression {}'.format(expr))
            except ValueError:
                raise ValueError('Unrecognized expression {}: '
            'currently supported expressions are "%(NA)>NUM".'.format(expr))
        # check input values
        if missing_ind_ge > 1.0 or missing_ind_ge < 0.0:
            raise ValueError('Invalid parameter "{}" for expression %(NA): '
                             'value should fall between 0 and 1'.format(missing_ind_ge))
        if missing_var_ge > 1.0 or missing_var_ge < 0.0:
            raise ValueError('Invalid parameter "{}" for expression %(NA): '
                             'value should fall between 0 and 1'.format(missing_var_ge))
        if missing_ind_ge == 0.0:
            missing_ind_ge = 1.0E-8
        if missing_var_ge == 0.0:
            missing_var_ge = 1.0E-8
        return missing_ind_ge, missing_var_ge

    def getAssoTests(self, methods, ncovariates, common_args):
        '''Get a list of methods from parameter methods, passing method specific and common
        args to its constructor. This function sets self.tests as a list of statistical tests'''
        if not methods:
            raise ValueError('Please specify at least one statistical test. '
                             'Please use command "vtools show tests" for a list of tests')
        tests = []
        for m in methods:
            name = m.split()[0]
            args = m.split()[1:] + common_args
            try:
                if '.' in name:
                    # if the method is defined elsewhere
                    m_module, m_name = name.split('.', 1)
                    # also search current working directory
                    my_dir = os.getcwd()
                    env.logger.info('Loading {} from {}'.format(m_module, my_dir))
                    if my_dir not in sys.path:
                        sys.path.append(my_dir)
                        # use the default level, which is -1 for python 2 and 0 for python 3
                        _temp = __import__(m_module, globals(), locals(), [m_name])
                        sys.path.pop()
                    else:
                        _temp = __import__(m_module, globals(), locals(), [m_name])
                    env.logger.info('Loading {}'.format(m_module))
                    method = getattr(_temp, m_name)(ncovariates, args)
                else:
                    method = eval(name)(ncovariates, args)
                # check if method is valid
                if not hasattr(method, 'fields'):
                    raise ValueError('Invalid association test method {}: '
                                     'missing attribute fields'.format(name))
                if not method.fields:
                    env.logger.warning('Association test {} has invalid or empty fields. '
                                        'No result will be generated.'.format(name))
                tests.append(method)
            except Exception as e:
                raise ValueError('Failed to load association test {0}: {1}.'
                                 'Please use command "vtools show tests" to list usable tests'.format(name, e))
        return tests

    def getPhenotype(self, condition, pheno, covar):
        '''Get a list of samples from specified condition.
        This function sets self.sample_IDs, self.phenotypes and self.covariates'''
        try:
            covar = [] if covar is None else covar
            cur = self.db.cursor()
            # handles missing phenotype automatically, but we need to first 
            # warn users what is happening
            #
            # we select samples with missing phenotype
            where_clause = 'WHERE ({})'.format(' OR '.join(['{} IS NULL'.format(x) for x in pheno + covar]))
            if condition:
                where_clause += ' '.join([' AND ({})'.format(x) for x in condition])
            cur.execute('SELECT sample_name, {} FROM sample LEFT OUTER JOIN filename ON sample.file_id = filename.file_id {}'.\
                format(', '.join(pheno + covar), where_clause))
            for rec in cur:
                for val, fld in zip(rec[1:], pheno + covar):
                    if val is None:
                        env.logger.warning('Sample {} is ignored due to missing value for phenotype {}'
                            .format(rec[0], fld))
            #
            # now we select samples without missing phenotype
            where_clause = 'WHERE {}'.format(' AND '.join(['{} IS NOT NULL'.format(x) for x in pheno + covar]))
            if condition:
                where_clause += ' '.join([' AND ({})'.format(x) for x in condition])
            query = 'SELECT sample_id, sample_name, {} FROM sample LEFT OUTER JOIN filename ON sample.file_id = filename.file_id {}'.\
                format(', '.join(pheno + covar), where_clause)
            env.logger.debug('Select phenotype and covariates using query {}'.format(query))
            cur = self.db.cursor()
            cur.execute(query)
            data = []
            for rec in cur:
                # get id, sample_name, phenotype, and covariants
                data.append([rec[0], rec[1], rec[2 : (2 + len(pheno))], rec[ (2 + len(pheno)) : ]])
            sample_IDs = []
            sample_names = []
            phenotypes = [[] for x in pheno]
            covariates = [[] for x in covar]
            for i, j, p, c in data:
                sample_IDs.append(i)
                sample_names.append(j)
                [x.append(y) for x,y in zip(phenotypes, p)]
                [x.append(y) for x,y in zip(covariates, c)]
            if len(sample_IDs) == 0:
                raise ValueError('No sample is selected by condition: {}'.\
                                 format(' AND '.join(['({})'.format(x) for x in condition])))
            else:
                if len(condition) > 0:
                    env.logger.info('{} samples are selected by condition: {}'.\
                                     format(len(sample_IDs), ' AND '.join(['({})'.format(x) for x in condition])))
                else:
                    env.logger.info('{} samples are found'.format(len(sample_IDs)))
            # add intercept
            covariates.insert(0, [1]*len(sample_IDs))
            try:
                phenotypes = [safeMapFloat(x, nan=False) for x in phenotypes]
                covariates = [safeMapFloat(x, nan=False) for x in covariates]
            except:
                raise ValueError('Invalid (non-numeric) coding in phenotype/covariates values')
            return sample_IDs, sample_names, phenotypes, covariates
        except Exception as e:
            env.logger.debug(e)
            if str(e).startswith('Invalid (non-numeric) coding'):
                raise ValueError(e)
            else:
                raise ValueError('Failed to retrieve phenotype {}{}{}. Please use command '
                    '"vtools show samples" to see a list of phenotypes'.format(', '.join(pheno),
                    '' if not covar else (' and/or covariate(s) ' + ', '.join(covar)),
                    '' if len(condition)==0 else (' under condition {}'.format(condition))))

    def identifyGroups(self, group_by):
        '''Get a list of groups according to group_by fields'''
        # do not write chr, pos to __asso_tmp by default
        if not group_by:
            group_by = ['variant.chr', 'variant.pos']
        # find the source of fields in group_by
        table_of_fields = [self.proj.linkFieldToTable(field, self.table)[-1].table for field in group_by]
        table_of_fields = [x if x else self.table for x in table_of_fields]
        # name the fields
        field_names = ['.'.join([x.rsplit('.', 1)[-1], y.rsplit('.', 1)[-1]]).replace('.', '_').lower() for x, y in zip(table_of_fields, group_by)]
        # type of the fields
        field_types = [self.db.typeOfColumn(y, x.rsplit('.', 1)[-1]) for x,y in zip(group_by, table_of_fields)]
        cur = self.db.cursor()
        #
        # create a table that holds variant ids and groups, indexed for groups.
        # The structure of this table will look like
        #
        #   variant_id  INT NOT NULL,
        #   chr VARCHAR(20),
        #   pos INT
        #
        cur.execute('DROP TABLE IF EXISTS __asso_tmp;')
        cur.execute('DROP INDEX IF EXISTS __asso_tmp_index;')
        # this table has
        #  variant_id
        #  groups (e.g. chr, pos)
        #  variant_info
        add_chrpos = True if (self.num_extern_tests > 0 and len([x for x in group_by if x in ['variant.chr', 'variant.pos', 'chr', 'pos']]) == 0) else False 
        create_query = '''
            CREATE TABLE __asso_tmp (
              variant_id INT NOT NULL, _ignored INT, 
              {} {} {});
              '''.format(
              ','.join(['{} {}'.format(x,y) for x,y in zip(field_names, field_types)]),
              ''.join([', {} FLOAT'.format(x.replace('.', '_')) for x in self.var_info]),
              ', variant_chr VARCHAR(20) NOT NULL, variant_pos INTEGER NOT NULL' if add_chrpos else '')
        cur.execute(create_query)
        # ['variant.chr', 'variant.pos'] must be in the var_info table if there is any ExternTest
        if add_chrpos:
            self.var_info += ['variant.chr', 'variant.pos']
        #
        # select variant_id and groups for association testing
        group_fields, fields = consolidateFieldName(self.proj, self.table, ','.join(group_by + self.var_info))
        from_clause = [self.table]
        where_clause = []
        fields_info = sum([self.proj.linkFieldToTable(x, self.table) for x in fields], [])
        #
        processed = set()
        for tbl, conn in [(x.table, x.link) for x in fields_info if x.table != '']:
            if (tbl.lower(), conn.lower()) not in processed:
                from_clause.append('{}'.format(tbl))
                where_clause.append('({})'.format(conn))
                processed.add((tbl.lower(), conn.lower()))
        #
        self.from_clause = ', '.join(from_clause)
        self.where_clause = ('WHERE ' + ' AND '.join(where_clause)) if where_clause else ''
        # This will be the tmp table to extract variant_id by groups (ignored is 0)
        query = 'INSERT INTO __asso_tmp SELECT DISTINCT {}.variant_id, 0, {} FROM {} {};'.\
          format(self.table, group_fields, self.from_clause, self.where_clause)
        s = delayedAction(env.logger.info, "Grouping variants by '{}', please be patient ...".\
                          format(':'.join(group_by)))
        env.logger.debug('Running query {}'.format(query))
        cur.execute(query)
        cur.execute('CREATE INDEX __asso_tmp_index ON __asso_tmp ({});'.\
                    format(','.join(['{} ASC'.format(x) for x in field_names])))
        del s
        # get group by
        cur.execute('SELECT DISTINCT {} FROM __asso_tmp;'.\
                    format(', '.join(field_names)))
        groups = cur.fetchall()
        env.logger.info('{} groups are found'.format(len(groups)))
        self.db.commit()
        #
        # the output can be too long
        #
        #env.logger.debug('Group by: {}'.format(':'.join(map(str, groups))))
        return field_names, field_types, groups


class GenotypeLoader(Process):
    '''This process loads genotypes in batch and send the results to a
    temporary sqlite database to be used later. This avoids numerous
    random access queies and significantly improves the performance of
    association tests. '''
    def __init__(self, param, ready_flags, index, queue, cached_samples):
        '''
        param: association test parameters, with sample ids.
        ready_flags: indicators if each loader has opened the database and
            ready for query.
        index: index of the current loader.
        queue: queue to get sample ids.
        cached_samples: tells the master process which sample has been
            processed, and by which loader.
        '''
        Process.__init__(self)
        self.proj = param.proj
        self.sample_IDs = param.sample_IDs
        self.geno_info = param.geno_info
        self.geno_name = param.geno_name
        self.geno_cond = param.genotypes
        self.group_names = param.group_names
        self.db_name = param.proj.name + '_genotype.DB'
        self.ready_flags = ready_flags
        self.index = index
        self.queue = queue
        self.cached_samples = cached_samples

    def run(self):
        # wait all processes to get ready
        while True:
            # if the previous ones have done, it is my turn to use db. We do this sequentially
            # to avoid database lock.
            if all(self.ready_flags[:self.index]) and not self.ready_flags[self.index]:
                # loading genotypes from genotype database
                db = DatabaseEngine()
                # readonly allows simultaneous access from several processes
                db.connect(self.db_name, readonly=False)
                db.attach('{0}.proj'.format(self.proj.name), '__fromVariants')
                # move __asso_tmp to :memory: can speed up the process a lot
                db.attach(':memory:', 'cache')
                # filling __asso_tmp, this is per-process so might use some RAM
                cur = db.cursor()
                # variant info of the original __asso_tmp table are not copied because they are not used.
                # we only copy records that are not ignored
                # No index is created for the in-ram database yet performance is satisfactory
                cur.execute('CREATE TABLE cache.__asso_tmp AS SELECT variant_id, {} '
                            'FROM __fromVariants.__asso_tmp WHERE _ignored = 0;'.\
                            format(', '.join(self.group_names)))
                # tells other processes that I am ready
                self.ready_flags[self.index] = 1
                env.logger.debug('Loader {} is ready'.format(self.index))
            if all(self.ready_flags):
                break
            time.sleep(random.random()*2)
        # these are written to different files so no lock is needed (lock=None)
        shelf = ShelfDB(os.path.join(env.temp_dir, 'geno_{0}'.format(self.index)),
                        'n', None)
        lenGrp = len(self.group_names)
        try:
            while True:
                id = self.queue.get()
                if id is None:
                    break
                try:
                    # sometimes on slow disks, SELECT would fail with error message
                    # "Cannot connect to database" if there are multiple reading processes.
                    # We are trying five times if this happens before we terminate the process
                    select_genotype_query = '''\
                    SELECT genotype_{0}.variant_id, {4} {1}, {2} FROM cache.__asso_tmp, genotype_{0} WHERE 
                    (cache.__asso_tmp.variant_id = genotype_{0}.variant_id{3}) ORDER BY {2}'''.\
                    format(id, ' '.join([', genotype_{0}.{1}'.format(id, x) for x in self.geno_info]),
                           ', '.join(['cache.__asso_tmp.{}'.format(x) for x in self.group_names]),
                           ' AND ({})'.format(' AND '.join(['({})'.format(x) for x in self.geno_cond])) if self.geno_cond else '',
                        self.geno_name)
                    select_genotype_msg = 'Load sample {} using genotype loader {}'.format(id, self.index)
                    executeUntilSucceed(cur, select_genotype_query, 5, select_genotype_msg)
                except OperationalError as e:
                    # flag the sample as missing
                    self.cached_samples[id] = -9
                    env.logger.error('Genotype loader {} failed to load sample {}: {}'.\
                                      format(self.index, id, e))
                    break
                # grab data for each group by
                data = {}
                cur_group = None
                for rec in cur:
                    grp = tuple(rec[-lenGrp:])
                    if cur_group != grp:
                        ## a new group
                        data[grp] = {rec[0]: rec[1:-lenGrp]}
                        cur_group = grp
                    else:
                        data[grp][rec[0]] = rec[1:-lenGrp]
                for grp,val in data.iteritems():
                    shelf.add('{},{}'.format(id, grp), val)
                # report progress, and tells the master process who owns the data
                self.cached_samples[id] = 1 + self.index
        except KeyboardInterrupt as e:
            # do not produce annoying traceback
            pass
        finally:
            db.close()
            # close shelf
            shelf.close()

class ResultRecorder:
    def __init__(self, params, db_name=None, delimiter=None, update_existing=False):
        self.succ_count = 0
        self.failed_count = 0
        #
        self.group_names = params.group_names
        self.fields = []
        self.group_fields = []
        for n,t in zip(params.group_names, params.group_types):
            self.group_fields.append(Field(name=n, index=None, type=t, adj=None, fmt=None, comment=n))
        self.fields.extend(self.group_fields)
        for test in params.tests:
            if test.name:
                self.fields.extend([
                    Field(name='{}_{}'.format(x.name, test.name), index=None,
                        type=x.type, adj=None, fmt=None, comment=x.comment) for x in test.fields])
            else:
                self.fields.extend(test.fields)
        for field in self.fields:
            if '-' in field.name:
                raise ValueError('"-" is not allowed in field name {}'.format(field.name))
        if len(self.fields) != len(set([x.name for x in self.fields])):
            raise ValueError('Duplicate field names. Please rename one of the tests using parameter --name')
        #
        self.printer = PrettyPrinter(delimiter=delimiter)
        self.printer.write([x.name for x in self.fields])
        self.writer = None
        if db_name:
            db_name = db_name if not db_name.lower().endswith('.db') else db_name[:-3]
            old_pragma = env.sqlite_pragma
            # make sure each commit will write data to disk, the performance can be bad though.
            env.sqlite_pragma = 'synchronous=FULL,journal_mode=DELETE'
            self.writer = AnnoDBWriter(db_name, self.fields,
                'field',                       # field annotation databases
                'Annotation database used to record results of association tests. Created on {}'.format(
                    time.strftime('%a, %d %b %Y %H:%M:%S', time.gmtime())),
                '1.0',                         # version 1.0
                {'*': self.group_names},       # link by group fields
                2,                             # database format
                True,                          # allow updating an existing database
                update_existing                # allow updating an existing field
            )
            # restore system sqlite_pragma
            env.sqlite_pragma = ','.join(old_pragma)
            #
            self.cur = self.writer.db.cursor()
            if self.writer.update_existing:
                #
                self.update_query = 'UPDATE {0} SET {1} WHERE {2};'.format(db_name,
                    ', '.join(['{}={}'.format(x.name, self.writer.db.PH) for x in self.fields[len(self.group_names):]]),
                    ' AND '.join(['{}={}'.format(x, self.writer.db.PH) for x in self.group_names]))
                self.insert_query = 'INSERT INTO {0} ({1}) VALUES ({2});'.format(db_name,
                    ','.join([x.name for x in self.fields]),
                    ','.join([self.writer.db.PH] * len(self.fields)))
                self.select_query = 'SELECT {1} FROM {0};'.format(db_name, ', '.join(self.group_names))
            else:
                self.insert_query = 'INSERT INTO {0} VALUES ({1});'.format(db_name,
                    ','.join([self.writer.db.PH] * len(self.fields)))
        #
        self.last_commit = time.time()

    def get_groups(self):
        '''Get groups that have been calculated'''
        self.cur.execute(self.select_query)
        return self.cur.fetchall()

    def record(self, res):
        self.succ_count += 1
        if len([x for x in res if x!=x]) == len(self.fields) - len(self.group_fields):
            # all fields are NaN: count this as a failure
            self.failed_count += 1
        else:
            self.printer.write(['{0:G}'.format(x, precision=5) if isinstance(x, float) else str(x) for x in res])
        # also write to an annotation database?
        if self.writer:
            if self.writer.update_existing:
                self.cur.execute(self.update_query, res[len(self.group_names):] + res[:len(self.group_names)])
                # if no record to update, insert a new one
                if self.cur.rowcount == 0:
                    self.cur.execute(self.insert_query, res)
            else:
                # insert a new record
                self.cur.execute(self.insert_query, res)
            # commit the records from time to time to write data to disk
            if time.time() - self.last_commit > 5:
                self.writer.db.commit()
                self.last_commit = time.time()

    def completed(self):
        return self.succ_count

    def failed(self):
        return self.failed_count

    def done(self):
        self.printer.write_rest()
        if self.writer:
            self.writer.finalize()


class AssoTestsWorker(Process):
    '''Association test calculator'''
    def __init__(self, param, grpQueue, resQueue, ready_flags, index, sampleMap, result_fields, shelf_lock):
        Process.__init__(self, name='Phenotype association analysis for a group of variants')
        self.param = param
        self.proj = param.proj
        self.table = param.table
        self.sample_IDs = param.sample_IDs
        self.phenotypes = param.phenotypes
        self.covariates = param.covariates
        self.phenotype_names = param.phenotype_names
        self.covariate_names = param.covariate_names
        self.var_info = param.var_info
        self.geno_info = param.geno_info
        self.tests = param.tests
        self.group_names = param.group_names
        self.missing_ind_ge = param.missing_ind_ge
        self.missing_var_ge = param.missing_var_ge
        self.sample_names = param.sample_names
        self.tests = param.tests
        self.num_extern_tests = param.num_extern_tests
        self.queue = grpQueue
        self.resQueue = resQueue
        self.ready_flags = ready_flags
        self.index = index
        self.sampleMap = sampleMap
        self.result_fields = result_fields
        self.shelf_lock = shelf_lock
        self.shelves = {}
        #
        self.db = DatabaseEngine()
        self.db.connect(param.proj.name + '_genotype.DB', readonly=True, lock=self.shelf_lock) 
        self.db.attach(param.proj.name + '.proj', '__fromVariant', lock=self.shelf_lock)
        #
        self.g_na = float('NaN')
        if env.treat_missing_as_wildtype:
            self.g_na = 0.0

    def __del__(self):
        self.db.close()
        for val in self.shelves.values():
            val.close()

    def getVarInfo(self, group, where_clause):
        var_info = {x:[] for x in self.var_info}
        query = 'SELECT variant_id {0} FROM __fromVariant.__asso_tmp WHERE ({1})'.format(
            ','+','.join([x.replace('.', '_') for x in self.var_info]) if self.var_info else '', where_clause)
        #env.logger.debug('Running query: {}'.format(query))
        cur = self.db.cursor()
        # SELECT can fail when the disk is slow which causes database lock problem.
        msg = 'Load variant info for group {} using association worker {}'.format(group, self.index)
        executeUntilSucceed(cur, query, 5, msg, group)
        #
        if not self.var_info:
            data = {x[0]:[] for x in cur.fetchall()}
        else:
            data = {x[0]:x[1:] for x in cur.fetchall()}
        variant_id = sorted(data.keys(), key=int)
        for idx, key in enumerate(self.var_info):
            if key not in ['variant.chr', 'variant.pos']:
                var_info[key] = [data[x][idx] if (type(data[x][idx]) in [int, float]) else float('NaN') for x in variant_id]
            else:
                var_info[key] = [data[x][idx] for x in variant_id] 
        return var_info, variant_id

    def getGenotype(self, group):
        '''Get genotype for variants in specified group'''
        # get variant_id
        where_clause = ' AND '.join(['{0}={1}'.format(x, self.db.PH) for x in self.group_names])
        cur = self.db.cursor()
        # variant info
        var_info, variant_id = self.getVarInfo(group, where_clause)
        # get genotypes / genotype info
        genotype = []
        geno_info = {x:[] for x in self.geno_info}
        # getting samples locally from my own connection
        for ID in self.sample_IDs:
            dbID = self.sampleMap[ID]
            if dbID not in self.shelves:
                try:
                    shelf = ShelfDB(os.path.join(env.temp_dir, 'geno_{}'.format(dbID)),
                                    'r', lock=self.shelf_lock)
                except Exception as e:
                    env.logger.error('Process {} failed to connect to shelf {}: {}'.\
                                      format(self.index, dbID, e))
                    raise
                self.shelves[dbID] = shelf
            else:
                shelf = self.shelves[dbID]
            #
            try:
                data = shelf.get('{},{}'.format(ID, group))
            except:
                # this sample might not have this group at all
                data = {}
            # handle missing values
            gtmp = [data.get(x, [self.g_na] + [float('NaN')]*len(self.geno_info)) for x in variant_id]
            # handle -1 coding (double heterozygotes)
            genotype.append([2.0 if x[0] == -1.0 else x[0] for x in gtmp])
            #
            # handle genotype_info
            #
            for idx, key in enumerate(self.geno_info):
                geno_info[key].append([x[idx+1] if (type(x[idx+1]) in [int, float]) else float('NaN') for x in gtmp])
        #
        # filter samples/variants for missingness
        gname = ':'.join(list(map(str, group)))
        return self.filterGenotype(genotype, geno_info, var_info, gname)

    def filterGenotype(self, genotype, geno_info, var_info, gname):
        '''
        Filter genotypes for missing calls or lack of minor alleles. Not very efficient because 
        it copies genotype, var_info and geno_info skipping the loci to be removed.
            - genotype is a Individual_list * Variants_list matrix of genotype values
            - var_info is a dictionary with each key being information corresponding Variant_list
            - geno_info is a dictionary with each key having a matrix of the same structure as genotype matrix
        '''
        # Step 1: filter individuals by genotype missingness at a locus
        missing_ratios = [sum(list(map(math.isnan, x))) / float(len(x)) for x in genotype]
        which = [x < self.missing_ind_ge for x in missing_ratios]
        # check for non-triviality of phenotype data
        if sum(which) < 5:
            raise ValueError("Sample size too small ({0}) to be analyzed for {1}.".format(sum(which), repr(gname)))
        if len(which) - sum(which) > 0:
            env.logger.debug('In {}, {} out of {} samples will be removed due to '
                              'having more than {}% missing genotypes'.\
                              format(repr(gname), len(which) - sum(which), len(which),
                                     self.missing_ind_ge * 100))
        # Step 2: filter variants by genotype missingness at a locus
        keep_loci = []
        for i in range(len(genotype[0])):
            # tag individuals missing variant calls
            missingness_vi = list(map(math.isnan, [x[i] for x, y in zip(genotype, which) if y]))
            # unique genotype codings on the locus
            gt_codings = list(set([x[i] for x, y in zip(genotype, which) if y and not math.isnan(x[i])]))
            keep_loci.append((float(sum(missingness_vi)) / float(len(missingness_vi))) < self.missing_var_ge and len(gt_codings) > 1)
        if len(keep_loci) - sum(keep_loci) > 0:
            for idx in range(len(genotype)):
                # filter genotype and geno_info
                genotype[idx] = [i for i, j in zip(genotype[idx], keep_loci) if j]
                for k in geno_info.keys():
                    geno_info[k][idx] = [i for i, j in zip(geno_info[k][idx], keep_loci) if j]
            # filter var_info
            for k in var_info.keys():
                var_info[k] = [i for i, j in zip(var_info[k], keep_loci) if j]
            #
            env.logger.debug('In {}, {} out of {} loci will be removed due to '
                              'having no minor allele or having more than {}% missing genotypes'.\
                              format(repr(gname), len(keep_loci) - sum(keep_loci),
                                     len(keep_loci), self.missing_ind_ge * 100))
        # check for non-triviality of genotype matrix
        if len(genotype[0]) == 0:
            raise ValueError("No variant found in genotype data for {}.".format(repr(gname)))
        return genotype, which, var_info, geno_info

    def setGenotype(self, which, data, info, grpname):
        geno = [x for idx, x in enumerate(data) if which[idx]]
        self.data.setGenotype(geno)
        self.data.setVar("gname", str(grpname))
        for field in info.keys():
            self.data.setVar('__geno_' + field, [x for idx, x in enumerate(info[field]) if which[idx]])

    def setPhenotype(self, which):
        '''Set phenotype data'''
        if len(self.phenotypes) > 1:
            raise ValueError('Only a single phenotype is allowed at this point')
        phen = [x for idx, x in enumerate(self.phenotypes[0]) if which[idx]]
        if self.covariates:
          covt = [[x for idx, x in enumerate(y) if which[idx]] for y in self.covariates]
        if self.covariates:
          self.data.setPhenotype(phen, covt)
        else:
          self.data.setPhenotype(phen)

    def setVarInfo(self, data):
        for field in data.keys():
            if field not in ['chr', 'pos']:
                self.data.setVar('__var_' + field, data[field])

    def setPyData(self, which, geno, var_info, geno_info,
                  missing_code, grpname, recode_missing = True):
        '''set all data to a python dictionary'''
        def fstr(x):
            try:
                float(x)
            except:
                x = str(x)
            return x
        #
        if len(self.phenotypes) > 1:
            raise ValueError('Only a single phenotype is allowed at this point')
        #
        self.pydata['name'] = grpname
        #
        if recode_missing:
            self.pydata['genotype'] = [[missing_code if math.isnan(e) else e for e in x] for idx, x in enumerate(geno) if which[idx]]
        else:
            self.pydata['genotype'] = [x for idx, x in enumerate(geno) if which[idx]]
        #
        try:
            self.pydata['coordinate'] = [(str(x), str(y)) for x, y in zip(var_info['variant.chr'], var_info['variant.pos'])]
        except:
            self.pydata['coordinate'] = []
        # var_info
        self.pydata['var_info'] = []
        self.pydata['var_info_header'] = []
        for k, item in var_info.items():
             if k != 'variant.chr' and k != 'variant.pos':
                 self.pydata['var_info_header'].append(k)
                 self.pydata['var_info'].append(map(fstr, item))
        self.pydata['var_info'] = zip(*self.pydata['var_info'])
        # geno_info
        self.pydata['geno_info'] = []
        self.pydata['geno_info_header'] = []
        for k, item in geno_info.items():
            self.pydata['geno_info_header'].append(k)
            if recode_missing:
                self.pydata['geno_info'].append([[missing_code if math.isnan(e) else e for e in x] for idx, x in enumerate(item) if which[idx]])
            else:
                self.pydata['geno_info'].append([x for idx, x in enumerate(item) if which[idx]])
        # convert geno_info to 3 dimensions:
        # D1: samples
        # D2: variants
        # D3: geno_info 
        self.pydata['geno_info'] = zip(*self.pydata['geno_info'])
        self.pydata['geno_info'] = [zip(*item) for item in self.pydata['geno_info']]
        unique_names = self.sample_names
        if len(self.sample_names) != len(set(self.sample_names)):
            env.logger.warning("Duplicated sample names found. Using 'sample_ID.sample_name' as sample names") 
            unique_names = ["{0}.{1}".format(i,s) for i,s in zip(self.sample_IDs, self.sample_names)]
        self.pydata['sample_name'] = [str(x) for idx, x in enumerate(unique_names) if which[idx]]
        self.pydata['phenotype_name'] = self.phenotype_names
        self.pydata['phenotype'] = [x for idx, x in enumerate(self.phenotypes[0]) if which[idx]]
        if self.covariates:
            self.pydata['covariate_name'] = self.covariate_names
            # skip the first covariate, a vector of '1''s
            self.pydata['covariates'] = [[x for idx, x in enumerate(y) if which[idx]] for y in self.covariates[1:]]
        #
        if len(self.pydata['genotype']) == 0 or len(self.pydata['phenotype']) == 0 or len(self.pydata['genotype'][0]) == 0:
            raise ValueError("No input data")
        if len(self.pydata['geno_info']) > 0 and len(self.pydata['genotype']) != len(self.pydata['geno_info']):
            raise ValueError("Genotype and genotype information do not match")


    def run(self):
        # 
        # tell the master process that this worker is ready
        self.ready_flags[self.index] = 1
        # wait all processes to e ready
        while True:
            if all(self.ready_flags):
                break
            else:
                time.sleep(random.random()*2)
        while True:
            # if cached, get genotype from the main process
            grp = self.queue.get()
            #
            try:
                grpname = ":".join(list(map(str, grp)))
            except TypeError:
                grpname = "None"
            if grp is None:
                break
            # env.logger.debug('Retrieved association unit {}'.format(repr(grpname)))
            #
            #
            self.data = t.AssoData()
            self.pydata = {}
            values = list(grp)
            try:
                # select variants from each group:
                genotype, which, var_info, geno_info = self.getGenotype(grp)
                # if I throw an exception here, the program completes in 5 minutes, indicating
                # the data collection part takes an insignificant part of the process.
                # 
                # set C++ data object
                if (len(self.tests) - self.num_extern_tests) > 0:
                    self.setGenotype(which, genotype, geno_info, grpname)
                    self.setPhenotype(which)
                    self.setVarInfo(var_info)
                # set Python data object, for external tests
                if self.num_extern_tests:
                    self.setPyData(which, genotype, var_info, geno_info, None, grpname)
                # association tests
                for test in self.tests:
                    test.setData(self.data, self.pydata)
                    result = test.calculate(env.association_timeout)
                    # env.logger.debug('Finished association test on {}'.format(repr(grpname)))
                    values.extend(result)
            except KeyboardInterrupt as e:
                # die silently if stopped by Ctrl-C
                break
            except Exception as e:
                env.logger.debug('An ERROR has occurred in process {} while processing {}: {}'.\
                                  format(self.index, repr(grpname), e))
                # self.data might have been messed up, create a new one
                self.data = t.AssoData()
                self.pydata = {}
                # return no result for any of the tests if an error message is captured.
                values.extend([float('NaN') for x in range(len(self.result_fields) - len(list(grp)))])
            self.resQueue.put(values)


def associate(args):
    try:
        with Project(verbosity=args.verbosity) as proj:
            # step 0: create an association testing object with all group information
            try:
                asso = AssociationTestManager(proj, args.variants, args.phenotypes, args.covariates,
                    args.var_info, args.geno_info, args.geno_name, args.methods, args.unknown_args,
                    args.samples, args.genotypes, args.group_by, args.discard_samples, args.discard_variants)
            except ValueError as e:
                sys.exit(e)
            if len(asso.groups) == 0:
                env.logger.info('No data to analyze.')
                sys.exit(0)
            # define results here but it might fail if args.to_db is not writable
            results = ResultRecorder(asso, args.to_db, args.delimiter, args.force)
            # determine if some results are already exist
            #
            # if write to a db and
            # if not forcefully recalculate everything and
            # the file exists
            # if the new fields is a subset of fields in the database
            if args.to_db and (not args.force) and results.writer.update_existing and \
                set([x.name for x in results.fields]).issubset(set([x.name for x in results.writer.cur_fields])):
                    existing_groups = results.get_groups()
                    num_groups = len(asso.groups)
                    asso.groups = list(set(asso.groups).difference(set(existing_groups)))
                    if len(asso.groups) != num_groups:
                        env.logger.info('{} out of {} groups with existing results are ignored. '
                                         'You can use option --force to re-analyze all groups.'.\
                                         format(num_groups - len(asso.groups), num_groups))
                        if len(asso.groups) == 0:
                            sys.exit(0)
                        # mark existing groups as ignored
                        cur = proj.db.cursor()
                        query = 'UPDATE __asso_tmp SET _ignored = 1 WHERE {}'.\
                          format(' AND '.join(['{}={}'.format(x, proj.db.PH) for x in asso.group_names]))
                        for grp in existing_groups:
                            cur.execute(query, grp)
                        proj.db.commit()
            sampleQueue = Queue()
            nJobs = max(min(args.jobs, len(asso.groups)), 1)
            # loading from disk cannot really benefit from more than 8 simultaneous read
            # due to disk access limits
            # if env.associate_num_of_readers is set we'll use it directly
            nLoaders = env.associate_num_of_readers
            # if no env.associate_num_of_readers is set we limit it to a max of 8.
            if not nLoaders > 0:
                nLoaders = min(8, nJobs)
            # step 1: getting all genotypes
            # the loaders can start working only after all of them are ready. Otherwise one
            # worker might block the database when others are trying to retrieve data
            # which is a non-blocking procedure.
            ready_flags = Array('L', [0]*nLoaders)
            # Tells the master process which samples are loaded, used by the progress bar.
            cached_samples = Array('L', max(asso.sample_IDs) + 1)
            #
            for id in asso.sample_IDs:
                sampleQueue.put(id)
            loaders = []
            for i in range(nLoaders):
                loader = GenotypeLoader(asso, ready_flags, i, sampleQueue, cached_samples)
                loader.start()
                loaders.append(loader)
                # None will kill the workers
                sampleQueue.put(None)
            #
            s = delayedAction(env.logger.info, "Starting {} processes to load genotypes".format(nLoaders))
            while True:
                if all(ready_flags):
                    break
                else:
                    time.sleep(random.random()*2)
            del s
            # progress bar...
            prog = ProgressBar('Loading genotypes', len(asso.sample_IDs))
            try:
                while True:
                    time.sleep(random.random()*2)
                    if sum([x == -9 for x in cached_samples]):
                        # some samples were not properly loaded
                        # the program has to quit because data integrity is compromised
                        prog.done()
                        env.logger.error('An error occurred while loading genotype data. '
                                          'Please make sure the genotype database is intact '
                                          'and is accessible before trying to start over.')
                        for loader in loaders:
                            loader.terminate()
                        proj.close()
                        sys.exit(1)
                    done = sum([x != 0 for x in cached_samples])
                    prog.update(done)
                    if done == len(asso.sample_IDs):
                        break
            except KeyboardInterrupt as e:
                env.logger.error('\nLoading genotype stopped by keyboard interruption.')
                proj.close()
                sys.exit(1)
            prog.done()
            for loader in loaders:
                loader.join()
            # step 1.5, start a maintenance process to create indexes, if needed.
            maintenance_flag = Value('L', 1)
            maintenance = MaintenanceProcess(proj, {'genotype_index': asso.sample_IDs}, maintenance_flag)
            maintenance.start()
            # step 2: workers work on genotypes
            # the group queue is used to send groups
            grpQueue = Queue()
            # the result queue is used by workers to return results
            resQueue = Queue()
            # see if all workers are ready
            ready_flags = Array('L', [0]*nJobs)
            shelf_lock = Lock()
            for j in range(nJobs):
                # the dictionary has the number of temporary database for each sample
                AssoTestsWorker(asso, grpQueue, resQueue, ready_flags, j, 
                    {x:y-1 for x,y in enumerate(cached_samples) if y > 0},
                    results.fields, shelf_lock).start()
            # send jobs ...
            # get initial completed and failed
            # put all jobs to queue, the workers will work on them
            for grp in asso.groups:
                grpQueue.put(grp)
            # the worker will stop once all jobs are finished
            for j in range(nJobs):
                grpQueue.put(None)
            #
            count = 0
            s = delayedAction(env.logger.info, "Starting {} association test workers".format(nJobs))
            while True:
                if all(ready_flags):
                    break
                else:
                    time.sleep(random.random()*2)
            del s
            prog = ProgressBar('Testing for association', len(asso.groups))
            try:
                while True:
                    # if everything is done
                    if count >= len(asso.groups):
                        break
                    # not done? wait from the queue and write to the result recorder
                    res = resQueue.get()
                    results.record(res)
                    # update progress bar
                    count = results.completed()
                    prog.update(count, results.failed())
                    # env.logger.debug('Processed: {}/{}'.format(count, len(asso.groups)))
            except KeyboardInterrupt as e:
                env.logger.error('\nAssociation tests stopped by keyboard interruption ({}/{} completed).'.\
                                  format(count, len(asso.groups)))
                results.done()
                proj.close()
                sys.exit(1)
            # finished
            prog.done()
            results.done()
            # summary
            env.logger.info('Association tests on {} groups have completed. {} failed.'.\
                             format(results.completed(), results.failed()))
            # use the result database in the project
            if args.to_db:
                proj.useAnnoDB(AnnoDB(proj, args.to_db, ['chr', 'pos'] if not args.group_by else args.group_by))
            # tells the maintenance process to stop
            maintenance_flag.value = 0
            # wait for the maitenance process to stop
            s = delayedAction(env.logger.info,
                              "Maintaining database. This might take a few minutes.", delay=10)
            maintenance.join()
            del s
    except Exception as e:
        env.logger.error(e)
        sys.exit(1)

def getAllTests():
    '''List all tests (all classes that subclasses of NullTest/GLMBurdenTest) in this module'''
    return sorted([(name, obj) for name, obj in globals().iteritems() \
        if type(obj) == type(NullTest) and issubclass(obj, NullTest) \
            and name not in ('NullTest', 'ExternTest', 'GLMBurdenTest',
                             'CaseCtrlBurdenTest', 'ScoreSeq')], key=lambda x: x[0])
