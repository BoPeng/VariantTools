/* =====================================================================================
// 
//  This is a small C and Python library for reading Plink genotype files,
//  written by Mattias Franberg, version 0.2.2 
//  
//  https://bitbucket.org/mattias_franberg/libplinkio
//
//  This software is not licensed or copyrighted. The varianttools developers
//  have been contacting its author and will include the license information when we
//  hear from the author, or replace it with alternative implementation if the author
//  requests for a removal.
// 
 ===================================================================================== */



union snp_lookup_t snp_lookup[256] =
{
    {{2, 2, 2, 2}},
    {{3, 2, 2, 2}},
    {{1, 2, 2, 2}},
    {{0, 2, 2, 2}},
    {{2, 3, 2, 2}},
    {{3, 3, 2, 2}},
    {{1, 3, 2, 2}},
    {{0, 3, 2, 2}},
    {{2, 1, 2, 2}},
    {{3, 1, 2, 2}},
    {{1, 1, 2, 2}},
    {{0, 1, 2, 2}},
    {{2, 0, 2, 2}},
    {{3, 0, 2, 2}},
    {{1, 0, 2, 2}},
    {{0, 0, 2, 2}},
    {{2, 2, 3, 2}},
    {{3, 2, 3, 2}},
    {{1, 2, 3, 2}},
    {{0, 2, 3, 2}},
    {{2, 3, 3, 2}},
    {{3, 3, 3, 2}},
    {{1, 3, 3, 2}},
    {{0, 3, 3, 2}},
    {{2, 1, 3, 2}},
    {{3, 1, 3, 2}},
    {{1, 1, 3, 2}},
    {{0, 1, 3, 2}},
    {{2, 0, 3, 2}},
    {{3, 0, 3, 2}},
    {{1, 0, 3, 2}},
    {{0, 0, 3, 2}},
    {{2, 2, 1, 2}},
    {{3, 2, 1, 2}},
    {{1, 2, 1, 2}},
    {{0, 2, 1, 2}},
    {{2, 3, 1, 2}},
    {{3, 3, 1, 2}},
    {{1, 3, 1, 2}},
    {{0, 3, 1, 2}},
    {{2, 1, 1, 2}},
    {{3, 1, 1, 2}},
    {{1, 1, 1, 2}},
    {{0, 1, 1, 2}},
    {{2, 0, 1, 2}},
    {{3, 0, 1, 2}},
    {{1, 0, 1, 2}},
    {{0, 0, 1, 2}},
    {{2, 2, 0, 2}},
    {{3, 2, 0, 2}},
    {{1, 2, 0, 2}},
    {{0, 2, 0, 2}},
    {{2, 3, 0, 2}},
    {{3, 3, 0, 2}},
    {{1, 3, 0, 2}},
    {{0, 3, 0, 2}},
    {{2, 1, 0, 2}},
    {{3, 1, 0, 2}},
    {{1, 1, 0, 2}},
    {{0, 1, 0, 2}},
    {{2, 0, 0, 2}},
    {{3, 0, 0, 2}},
    {{1, 0, 0, 2}},
    {{0, 0, 0, 2}},
    {{2, 2, 2, 3}},
    {{3, 2, 2, 3}},
    {{1, 2, 2, 3}},
    {{0, 2, 2, 3}},
    {{2, 3, 2, 3}},
    {{3, 3, 2, 3}},
    {{1, 3, 2, 3}},
    {{0, 3, 2, 3}},
    {{2, 1, 2, 3}},
    {{3, 1, 2, 3}},
    {{1, 1, 2, 3}},
    {{0, 1, 2, 3}},
    {{2, 0, 2, 3}},
    {{3, 0, 2, 3}},
    {{1, 0, 2, 3}},
    {{0, 0, 2, 3}},
    {{2, 2, 3, 3}},
    {{3, 2, 3, 3}},
    {{1, 2, 3, 3}},
    {{0, 2, 3, 3}},
    {{2, 3, 3, 3}},
    {{3, 3, 3, 3}},
    {{1, 3, 3, 3}},
    {{0, 3, 3, 3}},
    {{2, 1, 3, 3}},
    {{3, 1, 3, 3}},
    {{1, 1, 3, 3}},
    {{0, 1, 3, 3}},
    {{2, 0, 3, 3}},
    {{3, 0, 3, 3}},
    {{1, 0, 3, 3}},
    {{0, 0, 3, 3}},
    {{2, 2, 1, 3}},
    {{3, 2, 1, 3}},
    {{1, 2, 1, 3}},
    {{0, 2, 1, 3}},
    {{2, 3, 1, 3}},
    {{3, 3, 1, 3}},
    {{1, 3, 1, 3}},
    {{0, 3, 1, 3}},
    {{2, 1, 1, 3}},
    {{3, 1, 1, 3}},
    {{1, 1, 1, 3}},
    {{0, 1, 1, 3}},
    {{2, 0, 1, 3}},
    {{3, 0, 1, 3}},
    {{1, 0, 1, 3}},
    {{0, 0, 1, 3}},
    {{2, 2, 0, 3}},
    {{3, 2, 0, 3}},
    {{1, 2, 0, 3}},
    {{0, 2, 0, 3}},
    {{2, 3, 0, 3}},
    {{3, 3, 0, 3}},
    {{1, 3, 0, 3}},
    {{0, 3, 0, 3}},
    {{2, 1, 0, 3}},
    {{3, 1, 0, 3}},
    {{1, 1, 0, 3}},
    {{0, 1, 0, 3}},
    {{2, 0, 0, 3}},
    {{3, 0, 0, 3}},
    {{1, 0, 0, 3}},
    {{0, 0, 0, 3}},
    {{2, 2, 2, 1}},
    {{3, 2, 2, 1}},
    {{1, 2, 2, 1}},
    {{0, 2, 2, 1}},
    {{2, 3, 2, 1}},
    {{3, 3, 2, 1}},
    {{1, 3, 2, 1}},
    {{0, 3, 2, 1}},
    {{2, 1, 2, 1}},
    {{3, 1, 2, 1}},
    {{1, 1, 2, 1}},
    {{0, 1, 2, 1}},
    {{2, 0, 2, 1}},
    {{3, 0, 2, 1}},
    {{1, 0, 2, 1}},
    {{0, 0, 2, 1}},
    {{2, 2, 3, 1}},
    {{3, 2, 3, 1}},
    {{1, 2, 3, 1}},
    {{0, 2, 3, 1}},
    {{2, 3, 3, 1}},
    {{3, 3, 3, 1}},
    {{1, 3, 3, 1}},
    {{0, 3, 3, 1}},
    {{2, 1, 3, 1}},
    {{3, 1, 3, 1}},
    {{1, 1, 3, 1}},
    {{0, 1, 3, 1}},
    {{2, 0, 3, 1}},
    {{3, 0, 3, 1}},
    {{1, 0, 3, 1}},
    {{0, 0, 3, 1}},
    {{2, 2, 1, 1}},
    {{3, 2, 1, 1}},
    {{1, 2, 1, 1}},
    {{0, 2, 1, 1}},
    {{2, 3, 1, 1}},
    {{3, 3, 1, 1}},
    {{1, 3, 1, 1}},
    {{0, 3, 1, 1}},
    {{2, 1, 1, 1}},
    {{3, 1, 1, 1}},
    {{1, 1, 1, 1}},
    {{0, 1, 1, 1}},
    {{2, 0, 1, 1}},
    {{3, 0, 1, 1}},
    {{1, 0, 1, 1}},
    {{0, 0, 1, 1}},
    {{2, 2, 0, 1}},
    {{3, 2, 0, 1}},
    {{1, 2, 0, 1}},
    {{0, 2, 0, 1}},
    {{2, 3, 0, 1}},
    {{3, 3, 0, 1}},
    {{1, 3, 0, 1}},
    {{0, 3, 0, 1}},
    {{2, 1, 0, 1}},
    {{3, 1, 0, 1}},
    {{1, 1, 0, 1}},
    {{0, 1, 0, 1}},
    {{2, 0, 0, 1}},
    {{3, 0, 0, 1}},
    {{1, 0, 0, 1}},
    {{0, 0, 0, 1}},
    {{2, 2, 2, 0}},
    {{3, 2, 2, 0}},
    {{1, 2, 2, 0}},
    {{0, 2, 2, 0}},
    {{2, 3, 2, 0}},
    {{3, 3, 2, 0}},
    {{1, 3, 2, 0}},
    {{0, 3, 2, 0}},
    {{2, 1, 2, 0}},
    {{3, 1, 2, 0}},
    {{1, 1, 2, 0}},
    {{0, 1, 2, 0}},
    {{2, 0, 2, 0}},
    {{3, 0, 2, 0}},
    {{1, 0, 2, 0}},
    {{0, 0, 2, 0}},
    {{2, 2, 3, 0}},
    {{3, 2, 3, 0}},
    {{1, 2, 3, 0}},
    {{0, 2, 3, 0}},
    {{2, 3, 3, 0}},
    {{3, 3, 3, 0}},
    {{1, 3, 3, 0}},
    {{0, 3, 3, 0}},
    {{2, 1, 3, 0}},
    {{3, 1, 3, 0}},
    {{1, 1, 3, 0}},
    {{0, 1, 3, 0}},
    {{2, 0, 3, 0}},
    {{3, 0, 3, 0}},
    {{1, 0, 3, 0}},
    {{0, 0, 3, 0}},
    {{2, 2, 1, 0}},
    {{3, 2, 1, 0}},
    {{1, 2, 1, 0}},
    {{0, 2, 1, 0}},
    {{2, 3, 1, 0}},
    {{3, 3, 1, 0}},
    {{1, 3, 1, 0}},
    {{0, 3, 1, 0}},
    {{2, 1, 1, 0}},
    {{3, 1, 1, 0}},
    {{1, 1, 1, 0}},
    {{0, 1, 1, 0}},
    {{2, 0, 1, 0}},
    {{3, 0, 1, 0}},
    {{1, 0, 1, 0}},
    {{0, 0, 1, 0}},
    {{2, 2, 0, 0}},
    {{3, 2, 0, 0}},
    {{1, 2, 0, 0}},
    {{0, 2, 0, 0}},
    {{2, 3, 0, 0}},
    {{3, 3, 0, 0}},
    {{1, 3, 0, 0}},
    {{0, 3, 0, 0}},
    {{2, 1, 0, 0}},
    {{3, 1, 0, 0}},
    {{1, 1, 0, 0}},
    {{0, 1, 0, 0}},
    {{2, 0, 0, 0}},
    {{3, 0, 0, 0}},
    {{1, 0, 0, 0}},
    {{0, 0, 0, 0}}
};
