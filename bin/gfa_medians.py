#!/usr/bin/env python3

import argparse
import astropy.io.fits as fits
from astropy.table import Table
import glob
import numpy as np
import os

# might want to consider (or add an option for) removing shaded
# areas of focus cameras from the computation of the median pixel value

extnames = ['GUIDE0', 'FOCUS1', 'GUIDE2', 'GUIDE3', 'FOCUS4', 'GUIDE5', 'FOCUS6', 'GUIDE7', 'GUIDE8', 'FOCUS9']

def remove_overscan(image):
    sh = image.shape
    if sh[1] == 2248:
        _image = np.zeros((1032, 2048), dtype=float)
        _image[:, 0:1024] = image[:, 50:1074]
        _image[:, 1024:2048] = image[:, 1174:2198]
    return _image

def image_area_median(image, extname):
    im = remove_overscan(image)

    if extname.find('FOCUS') == -1:
        median = np.median(im)
    else:
        median = np.median(np.concatenate([np.ravel(im[:, 0:900]), np.ravel(im[:, 1170:2048])]))

    if median == np.floor(median):
        median = int(median)

    return median

def check_valid_extname(extname):
    if not extname in extnames:
        print('invalid extension name specified')
        assert(False)

def _expid_from_fname(fname):
    _fname = os.path.split(fname)[1]
    expid = int(_fname[4:12])

    return expid

def _get_file_list(night, basedir, min_expid, max_expid):

    assert(min_expid <= max_expid)

    flist = glob.glob(basedir + '/' + night + '/*/gfa*.fz')
 
    _flist = [os.path.split(f)[1] for f in flist]
    
    expid = np.array([_expid_from_fname(f) for f in _flist])

    flist = np.array(flist)

    flist = flist[(expid >= min_expid) & (expid <= max_expid)]

    return flist

if __name__ == "__main__":
    descr = 'print information about GFA image area median'
    parser = argparse.ArgumentParser(description=descr)

    parser.add_argument('night', type=str, nargs=1, 
                        help='NIGHT string')

    parser.add_argument('--basedir', default='/exposures/desi', type=str,
                        help='raw exposure base directory')

    parser.add_argument('--min_expid', default=-1, type=int,
                        help='minimum EXPID')

    parser.add_argument('--max_expid', default=10000000, type=int,
                        help='maximum EXPID')

    parser.add_argument('--expid', default=None, type=int,
                        help='run just exposure EXPID')

    parser.add_argument('--extname', default=None, type=str,
                        help='only analyze EXTNAME')

    args = parser.parse_args()

    min_expid = args.min_expid
    max_expid = args.max_expid

    if args.expid is not None:
        min_expid = args.expid
        max_expid = args.expid

    flist = _get_file_list(args.night[0], args.basedir, min_expid, max_expid)

    if args.extname is not None:
        _extnames = [args.extname]
    else:
        _extnames = extnames

    print('filename', 'extname', 'median (ADU)')
    print('====================================')

    result = []
    for i, fname in enumerate(flist):
        for extname in _extnames:
            # try/except handles case where not all GFA cameras present in an exposure
            try:
                im = fits.getdata(fname, extname=extname)
            except:
                continue
            median = image_area_median(im, extname)
            print(fname, extname, median)
            result.append((args.night[0], fname, _expid_from_fname(fname), extname, median))
        if i != (len(flist)-1):
            print('-')
