#!/usr/bin/env python
#
# $File: sample.py $
# $LastChangedDate: 2011-06-16 20:10:41 -0500 (Thu, 16 Jun 2011) $
# $Rev: 4234 $
#
# This file is part of variant_tools, a software application to annotate,
# summarize, and filter variants for next-gen sequencing ananlysis.
# Please visit http://variant_tools.sourceforge.net # for details.
#
# Copyright (C) 2004 - 2010 Bo Peng (bpeng@mdanderson.org)
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

import sys
from .project import Project
from .utils import ProgressBar, typeOfValues

class Sample:
    def __init__(self, proj):
        self.proj = proj
        self.logger = proj.logger
        self.db = proj.db

    def load(self, filename, allowed_fields, samples):
        '''Load phenotype informaiton from a file'''
        if not self.db.hasTable('sample'):
            self.logger.warning('Project does not have a sample table.')
            return
        # num sample, num new field, num update field
        count = [0, 0, 0]
        with open(filename) as input:
            headers = input.readline().rstrip().split('\t')
            if len(headers) == 0:
                raise ValueError('Empty header line. No phenotype will be imported.')
            if headers[0] == 'sample_name':
                by_sample = True
                if len(headers) == 1:
                    raise ValueError('No phenotype to be imported')
            elif len(headers) >= 2 and headers[0] == 'filename' and headers[1] == 'sample_name':
                by_sample = False
                if len(headers) == 2:
                    raise ValueError('No phenotype to be imported')
            else:
                raise ValueError('The phenotype file must start with a header line with the first '
                    'column sample_name, or first two fields being filename and sample_name.')
            #
            records = {}
            nCol = len(headers)
            for idx, line in enumerate(input.readlines()):
                if line.startswith('#') or line.strip() == '':
                    continue
                fields = [x.strip() for x in line.split('\t')]
                if len(fields) != nCol or ('' in fields):
                    raise ValueError('Invalid phenotype file: number of fields mismatch at line {}. Please use \'None\' for missing values.'.format(idx+2))
                #
                if by_sample:
                    key = (None if fields[0] == 'None' else fields[0],)
                    if key in records:
                        raise ValueError('Duplicate sample name ({}). Only the last record will be used'.format(key))
                    records[key] = fields[1:]
                else:
                    key = (fields[0], None if fields[1] == 'None' else fields[1])
                    if key in records:
                        raise ValueError('Duplicate filename and sample name ({},{}). Only the last record will be used'.format(key[0], key[1]))
                    records[key] = fields[2:]
            #
            new_fields = headers[(1 if by_sample else 2):]
            if allowed_fields:
                for f in allowed_fields:
                    if f.lower() not in [x.lower() for x in new_fields]:
                        raise ValueError('Field {} is not in specified input file {}'.format(f, filename))
        # 
        # get allowed samples
        cur = self.db.cursor()
        allowed_samples = self.proj.selectSampleByPhenotype(samples)
        if not allowed_samples:
            raise ValueError('No sample is selected using condition "{}"'.format(samples))
        #
        # get existing fields
        cur_fields = self.db.getHeaders('sample')[3:]
        # handle each field one by one
        for idx, field in enumerate(new_fields):
            if allowed_fields and field.lower() not in [x.lower() for x in allowed_fields]:
                self.logger.debug('Ignoring field {}'.format(field))
                continue
            # if adding a new field
            if field.lower() not in [x.lower() for x in cur_fields]:
                self.proj.checkFieldName(field, exclude='sample')
                fldtype = typeOfValues([x[idx] for x in records.values()])
                self.logger.info('Adding field {}'.format(field))
                self.db.execute('ALTER TABLE sample ADD {} {} NULL;'.format(field, fldtype))
                count[1] += 1  # new
            else:
                count[2] += 1  # updated
            for key, rec in records.iteritems():
                # get matching sample
                if by_sample:
                    cur.execute('SELECT sample.sample_id FROM sample WHERE sample_name = {}'.format(self.db.PH), key)
                    ids = [x[0] for x in cur.fetchall()]
                    if len(ids) == 0:
                        self.logger.warning('Sample name {} does not match any sample'.format(key[0]))
                        continue
                    for id in [x for x in ids if x in allowed_samples]:
                        count[0] += 1
                        cur.execute('UPDATE sample SET {0}={1} WHERE sample_id={1};'.format(field, self.db.PH), [rec[idx], id])
                else:
                    cur.execute('SELECT sample.sample_id FROM sample LEFT JOIN filename ON sample.file_id = filename.file_id WHERE filename.filename = {0} AND sample.sample_name = {0}'.format(self.db.PH), key)
                    ids = [x[0] for x in cur.fetchall()]
                    if len(ids) == 0:
                        self.logger.warning('Filename {} and sample name {} does not match any sample'.format(key[0], key[1]))
                        continue
                    if len(ids) != 1:
                        raise ValueError('Filename and sample should unqiuely determine a sample')
                    for id in [x for x in ids if x in allowed_samples]:
                        count[0] += 1
                        cur.execute('UPDATE sample SET {0}={1} WHERE sample_id={1};'.format(field, self.db.PH), [rec[idx], id])
        self.logger.info('{} field ({} new, {} existing) phenotypes of {} samples are updated.'.format(
            count[1]+count[2], count[1], count[2], count[0]/(count[1] + count[2])))
        self.db.commit()

    def setField(self, field, expression, genotypes, samples):
        '''Add a field using expression calculated from sample variant table'''
        IDs = self.proj.selectSampleByPhenotype(samples)
        if not IDs:
            raise ValueError('No sample is selected using condition "{}"'.format(samples))
        #
        count = [0, 0, 0]
        # if adding a new field
        cur_fields = self.db.getHeaders('sample')[3:]
        if field.lower() not in [x.lower() for x in cur_fields]:
            self.proj.checkFieldName(field, exclude='sample')
            self.logger.info('Adding field {}'.format(field))
            if expression.isdigit():  # all digit 
                fldtype = 'INT'
            elif (expression.startswith('"') and expression.endswith('"')) or \
                (expression.startswith("'") and expression.endswith("'")):
                fldtype = 'VARCHAR({})'.format(len(expression))
            else:
                fldtype = 'FLOAT'
            self.db.execute('ALTER TABLE sample ADD {} {} NULL;'.format(field, fldtype))
            count[1] += 1  # new
        else:
            count[2] += 1  # updated
        #
        cur = self.db.cursor()
        for ID in IDs:
            cur.execute('SELECT {} FROM {}_genotype.sample_variant_{} {};'\
                .format(expression, self.proj.name, ID, 'WHERE {}'.format(genotypes) if genotypes.strip() else ''))
            res = cur.fetchone()
            if len(res) == 0:
                raise ValueError('No statistics are calculated from expression {}'.format(expression))
            cur.execute('UPDATE sample SET {0}={1} WHERE sample_id = {1}'.format(field, self.db.PH), [res[0], ID])
            count[0] += 1
        self.logger.info('{} field ({} new, {} existing) phenotypes of {} samples are updated.'.format(
            count[1]+count[2], count[1], count[2], count[0]/(count[1] + count[2])))
        self.db.commit()


    def calcSampleStat(self, IDs, variant_table, genotypes, num, hom, het, other, other_stats):
        '''Count sample allele count etc for specified sample and variant table'''
        if not self.proj.isVariantTable(variant_table):
            raise ValueError('"Variant_table {} does not exist.'.format(variant_table))
        
        if num is None and hom is None and het is None and other is None and not other_stats:
            self.logger.warning('No statistics is specified')
            return

        coreDestinations = [num, hom, het, other]  

        # keys to speed up some operations
        MEAN = 0
        SUM = 1
        MIN = 2
        MAX = 3
        operationKeys = {}
        operationKeys['mean'] = MEAN
        operationKeys['sum'] = SUM
        operationKeys['min'] = MIN
        operationKeys['max'] = MAX
        possibleOperations = operationKeys.keys()
        
        operations = []
        genotypeFields = []
        validGenotypeFields = []
        destinations = []
        fieldCalcs = []   
        for index in range(0, len(other_stats), 2):
            if other_stats[index].startswith('--'):
                if other_stats[index].find('_') == -1:
                    raise ValueError('Unsupported operation {}.  Supported operations include {}.'.format(other_stats[index][2:], ', '.join(possibleOperations)))
                operation, field = other_stats[index][2:].split('_',1)
                if operation not in possibleOperations:
                    raise ValueError('Unsupported operation {}.  Supported operations include {}.'.format(operation, ', '.join(possibleOperations)))
                operations.append(operationKeys[operation])
                genotypeFields.append(field)
                fieldCalcs.append(None)
                if index + 1 >= len(other_stats) or other_stats[index + 1].startswith('--'):
                    raise ValueError('Missing or invalid field name following parameter {}'.format(other_stats[index]))
                destinations.append(other_stats[index + 1])
            else:
                raise ValueError('Expected to see an argument (e.g., --mean_FIELD) here, but found {} instead.'.format(other_stats[index]))
          
        #
        cur = self.db.cursor()
        if IDs is None:
            cur.execute('SELECT sample_id from sample;')
            IDs = [x[0] for x in cur.fetchall()]
        #
        numSample = len(IDs)
        if numSample == 0:
            return
        
        # Error checking with the user specified genotype fields
        # 1) if a field does not exist within one of the sample genotype tables a warning is issued
        # 2) if a field does not exist in any sample, it is not included in validGenotypeFields
        # 3) if no fields are valid and no core stats were requested (i.e., num, het, hom, other), then sample_stat is exited
        genotypeFieldTypes = {}
        for id in IDs:
            fields = self.proj.db.getHeaders('{}_genotype.sample_variant_{}'.format(self.proj.name, id))
            for field in fields:
                if genotypeFieldTypes.get(field) is None:
                    genotypeFieldTypes[field] = 'INT'
                    fieldType = self.db.typeOfColumn('{}_genotype.sample_variant_{}'.format(self.proj.name, id), field) 
                    if fieldType.upper().startswith('FLOAT'):
                        genotypeFieldTypes[field] = 'FLOAT'
                    elif fieldType.upper().startswith('VARCHAR'):
                        raise ValueError('Genotype field {} is a VARCHAR which is not supported with sample_stat operations.'.format(field))
            
        validGenotypeIndices = []
        for index in range(0,len(genotypeFields)):
            field = genotypeFields[index]
            if field not in genotypeFieldTypes.keys():
                self.logger.warning("Field {} is not an existing genotype field within your samples: {}".format(field, str(genotypeFieldTypes.keys())))
            else:
                validGenotypeIndices.append(index)
                validGenotypeFields.append(field)
        if num is None and het is None and hom is None and other is None and len(validGenotypeFields) == 0:
            self.logger.warning("No valid sample statistics operation has been specified.")
            return
        
        queryDestinations = coreDestinations
        for index in validGenotypeIndices:
            queryDestinations.append(destinations[index])
        for name in queryDestinations:
            if name is not None:
                self.proj.checkFieldName(name, exclude=variant_table)
                
        #
        from_variants = set()
        if variant_table != 'variant':
            self.logger.info('Getting variants from table {}'.format(variant_table))
            cur.execute('SELECT variant_id FROM {};'.format(variant_table))
            from_variants = set([x[0] for x in cur.fetchall()])
        #
        # too bad that I can not use a default dict...
        variants = dict()
        prog = ProgressBar('Counting variants',
            sum([self.db.numOfRows('{}_genotype.sample_variant_{}'.format(self.proj.name, id)) for id in IDs]))
        count = 0
        for id in IDs:
            whereClause = ''
            if genotypes is not None and len(genotypes) != 0:
                whereClause = 'where ' + ' AND '.join(genotypes)
            
            fieldSelect = ''
            if validGenotypeFields is not None and len(validGenotypeFields) != 0:
                fieldSelect = ', ' + ', '.join(validGenotypeFields)
            
            try:    
                query = 'SELECT variant_id, GT{} FROM {}_genotype.sample_variant_{} {};'.format(fieldSelect, self.proj.name, id, whereClause)
                cur.execute(query)
            except:
                self.logger.warning('Sample {} does not have all the requested genotype fields [{}].'.format(id, ', '.join(set(validGenotypeFields))))
                
            for rec in cur:
                if len(from_variants) == 0 or rec[0] in from_variants:
                    if rec[0] not in variants:
                        variants[rec[0]] = [0, 0, 0, 0]
                        variants[rec[0]].extend(list(fieldCalcs))

                    # type heterozygote
                    if rec[1] == 1:
                        variants[rec[0]][0] += 1
                    # type homozygote
                    elif rec[1] == 2:
                        variants[rec[0]][1] += 1
                    # type double heterozygote with two different alternative alleles
                    elif rec[1] == -1:
                        variants[rec[0]][2] += 1
                    # type wildtype
                    elif rec[1] == 0:
                        variants[rec[0]][3] += 1
                    elif rec[1] is None:
                        pass
                    else:
                        self.logger.warning('Invalid genotype type {}'.format(rec[1]))
                
                    # this collects genotype_field information
                    if len(validGenotypeFields) > 0:
                        for index in validGenotypeIndices:
                            queryIndex = index + 2     # to move beyond the variant_id and GT fields in the select statement
                            recIndex = index + 4       # first 4 attributes of variants are het, hom, double_het and wildtype
                            operation = operations[index]
                            field = genotypeFields[index]
                            if operation in [MEAN, SUM]:
                                if variants[rec[0]][recIndex] is None:
                                    variants[rec[0]][recIndex] = rec[queryIndex]
                                else:
                                    variants[rec[0]][recIndex] += rec[queryIndex]
                            if operation == MIN:
                                if variants[rec[0]][recIndex] is None or rec[queryIndex] < variants[rec[0]][recIndex]:
                                    variants[rec[0]][recIndex] = rec[queryIndex]
                            if operation == MAX:
                                if variants[rec[0]][recIndex] is None or rec[queryIndex] > variants[rec[0]][recIndex]:
                                    variants[rec[0]][recIndex] = rec[queryIndex]  
                    
                count += 1
                if count % self.db.batch == 0:
                    prog.update(count)
        prog.done()
        #
        headers = self.db.getHeaders(variant_table)
        table_attributes = [(num, 'INT'), (hom, 'INT'),
                (het, 'INT'), (other, 'INT')]
        fieldsDefaultZero = [num, hom, het, other]
        
        for index in validGenotypeIndices:
            if operations[index] == MEAN:
                table_attributes.append((destinations[index], 'FLOAT'))
            else:
                table_attributes.append((destinations[index], genotypeFieldTypes.get(genotypeFields[index])))
        for field, fldtype in table_attributes:
            defaultValue = None
            # We are setting default values on the count fields to 0.  The genotype stat fields are set to NULL by default.
            if field in fieldsDefaultZero: defaultValue = 0
            if field is None:
                continue
            if field in headers:
                self.logger.info('Updating existing field {}'.format(field))
                self.db.execute('Update {} SET {} = {};'.format(variant_table, field, defaultValue))
                if fldtype == 'FLOAT' and operations[index] != MEAN:
                    self.logger.warning('Result will be wrong if field \'{}\' was created to hold integer values'.format(field))
            else:
                self.logger.info('Adding field {}'.format(field))
                self.db.execute('ALTER TABLE {} ADD {} {} NULL;'.format(variant_table, field, fldtype))
                if defaultValue == 0:
                    self.db.execute ('UPDATE {} SET {} = 0'.format(variant_table, field))              
        #

        prog = ProgressBar('Updating table {}'.format(variant_table), len(variants))
        update_query = 'UPDATE {0} SET {2} WHERE variant_id={1};'.format(variant_table, self.db.PH,
            ' ,'.join(['{}={}'.format(x, self.db.PH) for x in queryDestinations if x is not None]))
        warning = False
        for count,id in enumerate(variants):
            value = variants[id]
            res = []
            if num is not None:
                # het + hom * 2 + other 
                res.append(value[0] + value[1] * 2 + value[2])
            if hom is not None:
                res.append(value[1])
            if het is not None:
                res.append(value[0])
            if other is not None:
                res.append(value[2])
                
            # for genotype_field operations, the value[operation_index] holds the result of the operation
            # except for the "mean" operation which needs to be divided by num_samples that have that variant
            for index in validGenotypeIndices:
                operationIndex = index + 4     # the first 3 indices hold the values for hom, het, double het and wildtype
                operationCalculation = value[operationIndex]
                if operations[index] == MEAN and operationCalculation is not None:
                    numSamples = value[0] + value[1] + value[2] + value[3]
                    operationCalculation /= float(numSamples)
                res.append(operationCalculation)
            cur.execute(update_query, res + [id])
            if count % self.db.batch == 0:
                self.db.commit()
                prog.update(count)
            
        self.db.commit()
        prog.done()
                
def phenotypeArguments(parser):
    '''Action that can be performed by this script'''
    parser.add_argument('-f', '--from_file', nargs='*',
        help='''Import phenotype from a tab delimited file. The file should have
            a header, with either 'sample_name' as the first column, or 'filename'
            and 'sample_name' as the first two columns. In the former case, samples
            with the same 'sample_name' will share the imported phenotypes. If 
            a list of phenotypes (columns of the file) is specified after filename,
            only the specified phenotypes will be imported. Parameter --samples
            could be used to limit the samples for which phenotypes are imported.'''),
    parser.add_argument('--set', nargs='*', default=[],
        help='''Set a phenotype to a summary statistics of a genotype field. For 
            example, '--set "num=count(*)"' sets phenotype num to be the number of
            genotypes of a sample, '--set "DP=avg(DP)"' sets phenotype DP to be the 
            average depth (if DP is one of the genotype fields) of the sample. Multiple
            fields (e.g. '--set "num=count(*)" "DP=avg(DP)"') and constant expressions
            (e.g. '--set aff=1') are also allowed. Parameters --genotypes and --samples
            could be used to limit the genotypes to be considered and the samples for
            which genotypes will be set.'''),
    parser.add_argument('-g', '--genotypes', nargs='*', default=[],
        help='''Limit the operation to genotypes that match specified conditions.
            Use 'vtools show genotypes' to list usable fields for each sample.'''),
    parser.add_argument('-s', '--samples', nargs='*', default=[],
        help='''Update phenotype for samples that match specified conditions.
            Use 'vtools show samples' to list usable fields in the sample table.''')

def phenotype(args):
    try:
        with Project() as proj:
            p = Sample(proj)
            if args.from_file:
                filename = args.from_file[0]
                fields = args.from_file[1:]
                p.load(filename, fields, ' AND '.join(args.samples))
            if args.set:
                proj.db.attach('{}_genotype'.format(proj.name))
                for item in args.set:
                    field, expr = [x.strip() for x in item.split('=', 1)]
                    if not expr:
                        raise ValueError('Invalid parameter {}, which should have format field=expression'.format(item))
                    p.setField(field, expr, ' AND '.join(args.genotypes), ' AND '.join(args.samples))
        proj.close()
    except Exception as e:
        sys.exit(e)
                

def sampleStatArguments(parser):
    '''Arguments to calculate sample statistics such as allele count'''
    parser.add_argument('-s', '--samples', nargs='*', default=[],
        help='''Limiting variants from samples that match conditions that
            use columns shown in command 'vtools show sample' (e.g. 'aff=1',
            'filename like "MG%%"').''')
    parser.add_argument('--genotypes', nargs='*', default=[],
        help='''Limiting variants from samples that match conditions that
            use columns shown in command 'vtools show genotypes' (e.g. 'GQ_INFO>15').''')
    parser.add_argument('table',
        help='''Variant table for which the statistics will be calculated and updated.''')
    parser.add_argument('-n', '--num',
        help='''Name of the field to hold number of alternative alleles in the sample.''')
    parser.add_argument('--hom',
        help='''Name of the field to hold number of samples with two identical alternative alleles.''')
    parser.add_argument('--het',
        help='''Name of the field to hold number of samples with one reference and one alternative alleles.''')
    parser.add_argument('--other',
        help='''Name of the field to hold number of samples with two different alternative alleles.''')
    
def sampleStat(args):
    try:
        with Project(verbosity=args.verbosity) as proj:
            # we save genotype in a separate database to keep the main project size tolerable.
            proj.db.attach(proj.name + '_genotype')
            variant_table = args.table if args.table else 'variant'
            if not proj.db.hasTable(variant_table):
                raise ValueError('Variant table {} does not exist'.format(variant_table))
            p = Sample(proj)
            IDs = None
            if args.samples:
                IDs = proj.selectSampleByPhenotype(' AND '.join(args.samples))
                if len(IDs) == 0:
                    p.logger.info('No sample is selected (or available)')
                    return
                else:
                    p.logger.info('{} samples are selected'.format(len(IDs)))
            p.calcSampleStat(IDs, variant_table, args.genotypes, args.num, args.hom,
                args.het, args.other, args.unknown_args)
        # temporary tables will be removed
        proj.close()
    except Exception as e:
        sys.exit(e)

