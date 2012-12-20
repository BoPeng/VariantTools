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



#ifndef __SNP_LOOKUP_H__
#define __SNP_LOOKUP_H__

#if HAVE_ENDIAN_H
#include <endian.h>
#elif HAVE_MACHINE_ENDIAN_H
#include <machine/endian.h>
#elif HAVE_SYS_ENDIAN_H
#include <sys/endian.h>
#endif

/**
 * This files contains a lookup table that maps
 * SNPs packed in a single byte into an array of
 * four bytes.
 */
union snp_lookup_t
{
    /**
     * Accessible as an array.
     */
    unsigned char snp_array[4];

    /**
     * Accessible as a block of bytes.
     */
    int32_t snp_block;
};

#if __BYTE_ORDER == __LITTLE_ENDIAN
#include "snp_lookup_little.h"
#else
#include "snp_lookup_big.h"
#endif /* End test endianess */

#endif /* End of __SNP_LOOKUP_H__ */