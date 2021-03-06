#!/usr/bin/env python

import os, argparse, pdb
import numpy as np
from glob import glob

import fitsio
import desispec.io
from desispec.calibfinder import CalibFinder
from desispec.fluxcalibration import isStdStar

rawdata_dir = os.path.join(os.getenv('DESI_ROOT'), 'spectro', 'data')
#reduxdir = os.path.join(os.getenv('DESI_ROOT'), 'spectro', 'redux', 'daily', 'exposures')
specprod_dir = os.path.join(os.getenv('DESI_ROOT'), 'spectro', 'redux', 'daily')
nightwatchdir = os.path.join(os.getenv('DESI_ROOT'), 'spectro', 'nightwatch', 'kpno')
outdir = os.path.join(os.getenv('DESI_ROOT'), 'spectro', 'redux', 'ioannis', 'cmx', 'fluxcalib')

def gather_nightwatch_qa(night, verbose=False, overwrite=False):
    """Read and stack all the nightwatch QA files for a given night.

    """
    import json
    import astropy.table
    from astropy.table import Table

    qadir = os.path.join(outdir, 'nightwatch')
    if not os.path.isdir(qadir):
        os.makedirs(qadir, exist_ok=True)
    stackwatchfile = os.path.join(qadir, 'qa-nightwatch-{}.fits'.format(str(night)))
    fiberassignmapfile = os.path.join(qadir, 'fiberassignmap-{}.fits'.format(str(night)))

    if os.path.isfile(stackwatchfile) and not overwrite:
        print('Reading {}'.format(stackwatchfile))
        data = Table(fitsio.read(stackwatchfile, 'PER_CAMFIBER'))
        fiberassignmap = Table(fitsio.read(fiberassignmapfile))
    else:
        #print('Reading the focal plane model.')
        #fp = desimodel.io.load_focalplane()[0]
        #fp = fp['PETAL', 'FIBER', 'OFFSET_X', 'OFFSET_Y']

        nightdir = os.path.join(nightwatchdir, str(night))
        allexpiddir = glob(os.path.join(nightdir, '????????'))

        data = []
        fiberassignmap = Table(names=('NIGHT', 'EXPID', 'TILEID', 'FIBERASSIGNFILE'),
                               dtype=('U8', 'U8', 'i4', 'U32'))
        for expiddir in allexpiddir:
            expid = os.path.basename(expiddir)
            qafile = os.path.join(expiddir, 'qa-{}.fits'.format(expid))

            qaFITS = fitsio.FITS(qafile)
            if 'PER_CAMFIBER' in qaFITS:
                if verbose:
                    print('Reading {}'.format(qafile))
                qa = Table(qaFITS['PER_CAMFIBER'].read())

            # Hack! Figure out the mapping between EXPID and FIBERMAPusing the request-EXPID.json file.
            requestfile = os.path.join(rawdata_dir, str(night), expid, 'request-{}.json'.format(expid))
            if not os.path.isfile(requestfile):
                print('Missing {}'.format(requestfile))
                continue
            with open(requestfile) as ff:
                req = json.load(ff)
            if 'PASSTHRU' in req.keys():
                if type(req['PASSTHRU']) is dict:
                    tileid = req['PASSTHRU']['TILEID']
                    #tileid = int(req['PASSTHRU'].split(':')[3].split(',')[0])
                else:
                    indx = req['PASSTHRU'].index('TILEID')
                    tileid = req['PASSTHRU'][indx:]
                    tileid = int(tileid[tileid.index(':')+1:tileid.index(',')])
                # This should use the svn checkout!
                tilefile = glob(os.path.join(rawdata_dir, str(night), '????????', 'fiberassign-{:06d}.fits'.format(tileid)))
                #if len(tilefile) == 0:
                #    print('No fibermap file found for EXPID={}'.format(expid))
                #if len(tilefile) > 0:
                #    print('Multiple fibermap files found for EXPID={}!'.format(expid))
                if len(tilefile) > 0:
                    tsplit = tilefile[0].split('/')
                    fiberassignmap.add_row((str(night), str(expid), tileid, os.path.join(tsplit[-2], tsplit[-1])))
                    #fiberassignmap[str(expid)] = [tileid]
                    data.append(qa)
                #else:
                #    print('  No tilefile found')
            #else:
            #    print('  No tilefile found')

        if len(data) == 0:
            print('No fiberassign files found for night {}'.format(night))
            return None, None
        data = astropy.table.vstack(data)

        # Need to update the data model to 'f4'.
        print('Updating the data model.')
        for col in data.colnames:
            if data[col].dtype == '>f8':
                data[col] = data[col].astype('f4')

        print('Writing {}'.format(stackwatchfile))
        fitsio.write(stackwatchfile, data.as_array(), clobber=True, extname='PER_CAMFIBER')

        print('Writing {}'.format(fiberassignmapfile))
        # ValueError: unsupported type 'U42'
        #fitsio.write(fiberassignmapfile, fiberassignmap.as_array(), clobber=True)
        fiberassignmap.write(fiberassignmapfile, overwrite=True)

    return data, fiberassignmap

def select_stdstars(data, fibermap, snrcut=10, verbose=False):
    """Select spectra based on S/N and targeting bit.

    """
    from astropy.table import Table
    from desitarget.targets import main_cmx_or_sv

    night, expid = data['NIGHT'][0], data['EXPID'][0]
    out_fibermap = fibermap.copy()

    # Pre-select standards
    istd = np.where(isStdStar(out_fibermap))[0]
    target_colnames, target_masks, survey = main_cmx_or_sv(out_fibermap)
    target_col, target_mask = target_colnames[0], target_masks[0] # CMX_TARGET, CMX_MASK for commissioning
    #istd = np.where((out_fibermap[target_col] & target_mask.mask('STD_BRIGHT') != 0))[0]
    
    # For each standard, apply a minimum S/N cut (in any camera) to
    # subselect the fibers that were reasonably centered on the fiber.
    reject = np.ones(len(istd), dtype=bool)
    #if len(istd) == 0:
    #    pdb.set_trace()
        
    if len(istd) > 0:
        for ii, fiber in enumerate(out_fibermap['FIBER'][istd]):
            wspec = np.where((data['EXPID'] == expid) * (data['FIBER'] == fiber))[0]
            if len(wspec) > 0:
                isnr = np.where(data['MEDIAN_CALIB_SNR'][wspec] > snrcut)[0]
                if len(isnr) > 0:
                    reject[ii] = False
                    
    if np.sum(~reject) > 0:
        istd_keep = istd[~reject]
    else:
        istd_keep = []
    if np.sum(reject) > 0:
        istd_reject = istd[reject]
    else:
        istd_reject = []
        
    # Hack! Set the targeting bit for the failed standards to zero!
    if len(istd_reject) > 0:
        out_fibermap[target_col][istd_reject] = 0

    if verbose:
        print('EXPID={}, NSTD={}/{}'.format(expid, len(istd_keep), len(istd)))

    return out_fibermap

def update_frame_fibermaps(night, verbose=False, overwrite=False):
    """Update the fibermaps in each frame file with the necessary information on
    targeted standard stars.

    """
    from astropy.table import Table
    from desitarget.targets import main_cmx_or_sv
    
    # Build the stacked QA and fiberassign "map" files.
    data, fiberassignmap = gather_nightwatch_qa(night, verbose=verbose, overwrite=overwrite)
    if data is None:
        return

    # For each EXPID, find the fibers with standard stars *and* good spectra.
    fibermapdict = dict()
    for expid in set(data['EXPID']):
        strexpid = '{:08d}'.format(expid)
        framefiles = sorted(glob(desispec.io.findfile(
            'frame', night=night, expid=expid,
            specprod_dir=specprod_dir, camera='*')))
        # Figure out which framefiles need to be (re)processed.
        if overwrite:
            need_framefiles = framefiles
        else:
            need_framefiles = []
            for framefile in framefiles:
                outframefile = framefile.replace(specprod_dir, outdir)
                if not os.path.isfile(outframefile):
                    need_framefiles.append(framefile)
            need_framefiles = np.array(need_framefiles)

        if len(need_framefiles) == 0:
            print('All done with NIGHT={}, EXPID={}'.format(night, strexpid))
            continue

        # Read and cache the fibermap.
        thismap = np.where((fiberassignmap['EXPID'] == strexpid) * (fiberassignmap['NIGHT'] == str(night)))[0]
        tileid, fibermapfile = fiberassignmap['TILEID'][thismap][0], fiberassignmap['FIBERASSIGNFILE'][thismap][0]
        strtileid = str(tileid)
        if strtileid in fibermapdict.keys():
            print('Using cached fibermap for TILEID={}'.format(strtileid))
            fullfibermap = fibermapdict[strtileid]
        else:
            fibermapfile = os.path.join(rawdata_dir, str(night), fibermapfile)
            if verbose:
                print('Reading and caching {}'.format(fibermapfile))
            fullfibermap = Table(fitsio.read(fibermapfile))#, columns=['CMX_TARGET']))
            fibermapdict[strtileid] = fullfibermap

        # Select standard stars
        thisspec = np.where(np.logical_and(data['EXPID'] == expid, data['NIGHT'] == night))[0]
        if len(thisspec) == 0:
            print('This should never happen!')
            pdb.set_trace()
        use_fullfibermap = select_stdstars(data[thisspec], fullfibermap, verbose=verbose)
        #check = np.where(isStdStar(use_fullfibermap))[0]
        #if len(check) == 0:
        #    pdb.set_trace()

        # Finally process every frame containing at least one standard star.
        for framefile in framefiles:
            outframefile = framefile.replace(specprod_dir, outdir)
            # Shouldn't need this bit of code because we checked for existing
            # files above.
            if os.path.isfile(outframefile) and not overwrite:
                print('File exists {}'.format(outframefile))
                continue

            fibers = fitsio.read(framefile, 'FIBERMAP', columns='FIBER')
            fibermap = use_fullfibermap[np.isin(use_fullfibermap['FIBER'], fibers)]
            #fibermap = fullfibermap[np.isin(fullfibermap['FIBER'], fr.fibermap['FIBER'])]

            # Require at least one standard star on this petal.
            #target_colnames, target_masks, survey = main_cmx_or_sv(fibermap)
            #target_col, target_mask = target_colnames[0], target_masks[0] # CMX_TARGET, CMX_MASK for commissioning
            #istd = np.where((fibermap[target_col] & target_mask.mask('STD_BRIGHT') != 0))[0]
            istd = np.where(isStdStar(fibermap))[0]
            if len(istd) > 0:
                # Read and copy all the columns!
                #if verbose:
                #    print('Reading {}'.format(framefile))
                fr = desispec.io.read_frame(framefile)

                #import matplotlib.pyplot as plt
                #plt.plot(fr.wave, fr.flux[istd[0], :]) ; plt.show()
                #pdb.set_trace()

                # Fragile. desi_proc should be smarter about how it initializes
                # the dummy fibermap and/or we should be smarter about what
                # columns we assume that desi_proc has added...
                isky = np.where(fr.fibermap['OBJTYPE'] == 'SKY')[0]
                if len(isky) == 0:
                    print('No sky fibers found -- this should not happen!')
                    continue
                fibermap['OBJTYPE'][isky] = fr.fibermap['OBJTYPE'][isky] # fragile!
                fibermap['DESI_TARGET'][isky] |= fr.fibermap['DESI_TARGET'][isky] # fragile!
                fr.fibermap = fibermap

                if verbose:
                    print('Writing {}'.format(outframefile))
                desispec.io.write_frame(outframefile, fr)

                # Also create a soft link to the skymodel file so we can run pipeline QA.
                skymodelfile = desispec.io.findfile('sky', night=night, expid=expid,
                                                    camera=fr.meta['CAMERA'],
                                                    specprod_dir=specprod_dir) # note: specprod_dir!
                cmd = 'ln -sf {} {}'.format(skymodelfile, skymodelfile.replace(specprod_dir, outdir))
                print(cmd)
                os.system(cmd)

        if False:
            print('Returning after first EXPID - fix me.')
            return

def fluxcalib(night, verbose=False, overwrite=False):
    """Fit the standards on each petal and then perform flux-calibration.

    desi_fit_stdstars --frames 00028833/frame-*.fits --skymodels 00028833/sky-*.fits \
      --fiberflats $DESI_SPECTRO_CALIB/spec/sp3/fiberflat-sm4-*-20191108.fits \
      --starmodels $DESI_BASIS_TEMPLATES/stdstar_templates_v2.2.fits --outfile stdstars-3-00028833.fits

    desi_compute_fluxcalibration  --infile 00028833/frame-b3-*.fits --sky 00028833/sky-b3-*.fits \
      --fiberflat $DESI_SPECTRO_CALIB/spec/sp3/fiberflat-sm4-b-20191108.fits --models stdstars-3-00028833.fits \
      --outfile fluxcalib-b3-00028833.fits --delta-color-cut 12

    """
    starmodelfile = os.path.join(os.getenv('DESI_BASIS_TEMPLATES'), 'stdstar_templates_v2.2.fits')
    
    allexpiddir = glob(os.path.join(outdir, 'exposures', str(night), '????????'))
    for expiddir in allexpiddir:
        expid = os.path.basename(expiddir)
        
        # Process each spectrograph separately.
        for spectro in np.arange(10):
            framefiles = sorted(glob(desispec.io.findfile(
                'frame', night=night, expid=int(expid), spectrograph=spectro,
                camera='*{}'.format(spectro), specprod_dir=outdir)))
            if len(framefiles) == 0:
                print('No frame files found for spectrograph {}'.format(spectro))
                continue

            # Gather the calibration files.
            havefiles = True if len(framefiles) == 3 else False
            if havefiles:
                fiberflatfiles, skymodelfiles = [], []
                for framefile in framefiles:
                    hdr = fitsio.read_header(framefile)
                    calib = CalibFinder([hdr])
                    
                    fiberflatfile = os.path.join(os.getenv('DESI_SPECTRO_CALIB'), calib.data['FIBERFLAT'])
                    skymodelfile = desispec.io.findfile(
                        'sky', night=night, expid=int(expid), spectrograph=spectro,
                        camera=hdr['CAMERA'].strip(), specprod_dir=outdir)
                    if not os.path.isfile(fiberflatfile) or not os.path.isfile(skymodelfile):
                        havefiles = False
                    else:
                        fiberflatfiles.append(fiberflatfile)
                        skymodelfiles.append(skymodelfile) 

            # Fit the standard stars (per-spectrograph, all three cameras simultaneously).
            stdstarsfile = desispec.io.findfile('stdstars', night=night, expid=int(expid),
                                                spectrograph=spectro, specprod_dir=outdir)
            if os.path.isfile(stdstarsfile) and not overwrite:
                print('File exists {}'.format(stdstarsfile))
            else:
                if havefiles:
                    cmd = 'desi_fit_stdstars --frames {framefiles} --skymodels {skymodelfiles} '
                    cmd += '--fiberflats {fiberflatfiles} --starmodels {starmodelfile} --ncpu 8 '
                    cmd += '--outfile {stdstarsfile}'
                    cmd = cmd.format(framefiles=' '.join(framefiles),
                                     skymodelfiles=' '.join(skymodelfiles),
                                     fiberflatfiles=' '.join(fiberflatfiles),
                                     starmodelfile=starmodelfile,
                                     stdstarsfile=stdstarsfile)
                    #print(cmd)
                    os.system(cmd)

            # Perform flux-calibration (per-spectrograph).
            if os.path.isfile(stdstarsfile) and havefiles:
                for iframe in np.arange(len(framefiles)):
                    framefile = framefiles[iframe]
                    camera = fitsio.read_header(framefile)['CAMERA'].strip()

                    outcalibfile = desispec.io.findfile(
                        'fluxcalib', night=night, expid=int(expid), spectrograph=spectro,
                        camera=camera, specprod_dir=outdir)
                    if os.path.isfile(outcalibfile) and not overwrite:
                        print('File exists {}'.format(outcalibfile))
                        continue
                    
                    ## Gather the calibration files.
                    #skyfile = framefile.replace(outdir, reduxdir).replace('frame-', 'sky-')
                    #calib = CalibFinder([fitsio.read_header(framefile)])
                    #fiberflatfile = os.path.join(os.getenv('DESI_SPECTRO_CALIB'), calib.data['FIBERFLAT'])

                    cmd = 'desi_compute_fluxcalibration --infile {framefile} --sky {skyfile} '
                    cmd += '--fiberflat {fiberflatfile} --models {stdstarsfile} --outfile {outcalibfile} '
                    cmd += '--delta-color-cut 12'
                    cmd = cmd.format(framefile=framefiles[iframe], skyfile=skymodelfiles[iframe],
                                     fiberflatfile=fiberflatfiles[iframe], stdstarsfile=stdstarsfile,
                                     outcalibfile=outcalibfile)
                    #print(cmd)
                    os.system(cmd)

def process_exposures(night, verbose=False, overwrite=False):
    """Process all exposures and write out cFrame files.

    usage: desi_process_exposure [-h] -i INFILE [--fiberflat FIBERFLAT]
                                 [--sky SKY] [--calib CALIB] -o OUTFILE
                                 [--cosmics-nsig COSMICS_NSIG]
                                 [--sky-throughput-correction]
    Apply fiberflat, sky subtraction and calibration.
    optional arguments:
      -h, --help            show this help message and exit
      -i INFILE, --infile INFILE
                            path of DESI exposure frame fits file
      --fiberflat FIBERFLAT
                            path of DESI fiberflat fits file
      --sky SKY             path of DESI sky fits file
      --calib CALIB         path of DESI calibration fits file
      -o OUTFILE, --outfile OUTFILE
                            path of DESI sky fits file
      --cosmics-nsig COSMICS_NSIG
                            n sigma rejection for cosmics in 1D (default, no
                            rejection)
      --sky-throughput-correction
                            apply a throughput correction when subtraction the sky

    """
    allexpiddir = glob(os.path.join(outdir, 'exposures', str(night), '????????'))
    for expiddir in allexpiddir:
        expid = os.path.basename(expiddir)

        framefiles = sorted(glob(desispec.io.findfile(
            'frame', night=night, expid=int(expid),
            camera='*', specprod_dir=outdir)))
        for framefile in framefiles:
            outframefile = framefile.replace('frame-', 'cframe-')
            if os.path.isfile(outframefile) and not overwrite:
                print('File exists {}'.format(outframefile))
                continue

            # Gather the calibration files.
            hdr = fitsio.read_header(framefile)
            camera = hdr['CAMERA'].strip()
            calib = CalibFinder([hdr])
                    
            fiberflatfile = os.path.join(os.getenv('DESI_SPECTRO_CALIB'), calib.data['FIBERFLAT'])
            skymodelfile = desispec.io.findfile('sky', night=night, expid=int(expid),
                                                camera=camera, specprod_dir=outdir)
            fluxcalibfile = desispec.io.findfile('fluxcalib', night=night, expid=int(expid), 
                                                 camera=camera, specprod_dir=outdir)
            if os.path.isfile(fiberflatfile) and os.path.isfile(skymodelfile) and os.path.isfile(fluxcalibfile):
                cmd = 'desi_process_exposure --infile {framefile} --sky {skymodelfile} '
                cmd += '--fiberflat {fiberflatfile} --calib {fluxcalibfile} '
                cmd += '--cosmics-nsig 6 --outfile {outframefile} '
                cmd = cmd.format(framefile=framefile, skymodelfile=skymodelfile,
                                 fiberflatfile=fiberflatfile, fluxcalibfile=fluxcalibfile,
                                 outframefile=outframefile)
                #print(cmd)
                os.system(cmd)
            else:
                print('Missing calibration files.')

def qa_stdstars(night, verbose=False, overwrite=False):
    """Make some QAplots of the standard stars.

    """
    import matplotlib.pyplot as plt
    import seaborn as sns
    sns.set(context='talk', style='ticks')#, font_scale=1.1)
    
    from astropy.table import Table, vstack
    #from desispec.io.fluxcalibration import read_flux_calibration, read_stdstar_models
    
    pngfile = os.path.join(outdir, 'qa', 'qa-stdstars-{}.png'.format(str(night)))
    if os.path.isfile(pngfile) and not overwrite:
        print('File exists {}'.format(pngfile))
        return

    allexpiddir = glob(os.path.join(outdir, 'exposures', str(night), '????????'))

    stdmeta = []
    for expiddir in allexpiddir:
        expid = os.path.basename(expiddir)
        for spectro in np.arange(10):
            stdstarsfile = desispec.io.findfile('stdstars', night=night, expid=int(expid),
                                                spectrograph=spectro, specprod_dir=outdir)
            if os.path.isfile(stdstarsfile):
                meta = Table(fitsio.read(stdstarsfile, 'METADATA'))
                meta['NIGHT'] = np.repeat(night, len(meta))
                meta['EXPID'] = np.repeat(expid, len(meta))
                meta['SPECTRO'] = np.repeat(spectro, len(meta))
                stdmeta.append(meta)
    if len(stdmeta) == 0:
        print('No good standard stars on night {}'.format(night))
        return
        
    stdmeta = vstack(stdmeta)
    print(stdmeta)

    xx, yy = stdmeta['DATA_G-R'], stdmeta['MODEL_G-R']
    lim = (np.min((xx.min(), yy.min()))-0.05, np.max((xx.max(), yy.max()))+0.05)
    lim = (0.05, 0.75)

    mm = ('o', 's', 'v', '^', '+', 'p', 'x', '*', '<', '>')
    s2mm = dict()
    for ii, ss in enumerate(np.arange(10).astype(str)):
        s2mm[ss] = mm[ii]

    fig, ax = plt.subplots()
    for spectro in sorted(set(stdmeta['SPECTRO'])):
        ww = stdmeta['SPECTRO'] == spectro
        xx, yy = stdmeta['DATA_G-R'][ww], stdmeta['MODEL_G-R'][ww]
        ax.scatter(xx, yy, edgecolors='k', alpha=0.8, label='SP{}'.format(spectro),
                   s=70, marker=s2mm[str(spectro)])
    ax.plot([lim[0], lim[1]], [lim[0], lim[1]], color='k', lw=2, alpha=0.5)
    ax.set_xlim(lim)
    ax.set_ylim(lim)
    ax.set_xlabel(r'$g-r$ (Data)')
    ax.set_ylabel(r'$g-r$ (Model)')
    ax.set_title('Night {}, NSTD={}'.format(night, len(stdmeta)))
    ax.legend(loc='lower right')

    fig.subplots_adjust(left=0.18, bottom=0.18)

    print('Writing {}'.format(pngfile))
    fig.savefig(pngfile)
    
#        cframefiles = sorted(glob(desispec.io.findfile(
#            'cframe', night=night, expid=int(expid),
#            camera='*', specprod_dir=outdir)))
#        if len(cframefiles) != 3:
#            continue
#        for cframefile in cframefiles:
#            framefile = cframefile.replace('cframe-', 'frame-')
#
#            from desispec.qa.qa_frame import qaframe_from_frame
#            qaframe_from_frame(framefile, output_dir=outdir, specprod_dir=outdir, make_plots=True, clobber=True)
#            pdb.set_trace()
#            
#            hdr = fitsio.read_header(cframefile)
#            camera = hdr['CAMERA'].strip()
#            if camera[0] == 'b':
#                spectro = int(camera[1])
#                stdstarsfile = desispec.io.findfile('stdstars', night=night, expid=int(expid),
#                                                    spectrograph=spectro, specprod_dir=outdir)
#                print(stdstarsfile)
#                stdflux, stdwave, stdfibers, _stdmeta = read_stdstar_models(stdstarsfile)
#                stdmeta.append(Table(_stdmeta))
#
#            fluxcalibfile = desispec.io.findfile('fluxcalib', night=night, expid=int(expid), 
#                                                 camera=camera, specprod_dir=outdir) # note: outdir!
#            fluxcalib = read_flux_calibration(fluxcalibfile)
#
#            fr = desispec.io.read_frame(cframefile)
            
def qa_throughput(night, verbose=False, overwrite=False):
    """Make some QAplots of the throughput.

    """
    import astropy.units as u
    from astropy import constants as const
    import matplotlib.pyplot as plt

    import seaborn as sns
    sns.set(context='talk', style='ticks')#, font_scale=1.0)

    import desimodel.io
    
    pngfile = os.path.join(outdir, 'qa', 'qa-throughput-{}.png'.format(str(night)))
    if os.path.isfile(pngfile) and not overwrite:
        print('File exists {}'.format(pngfile))
        return

    desi = desimodel.io.load_desiparams()
    area = (desi['area']['geometric_area'] * u.m**2).to(u.cm**2)

    thru = dict()
    for camera in ('b', 'r' , 'z'):
        thru[camera] = desimodel.io.load_throughput(camera)
        #import specter.throughput
        #thrufile = '/global/u2/i/ioannis/repos/desihub/desimodel/data/throughput/thru-{}.fits'.format(camera)
        #thru[camera] = specter.throughput.load_throughput(thrufile)

    fig, ax = plt.subplots(figsize=(8, 6))
    
    allexpiddir = sorted(glob(os.path.join(outdir, 'exposures', str(night), '????????')))
    colors = iter(plt.cm.rainbow(np.linspace(0, 1, len(allexpiddir))))

    nstar = 0
    for expiddir in allexpiddir:
        expid = os.path.basename(expiddir)

        for spectro in np.arange(10):
            #calibfiles = desispec.io.findfile(
            #    'fluxcalib', night=night, expid=int(expid), spectrograph=spectro,
            #    camera=camera, specprod_dir=outdir)
            
            calibfiles = glob(os.path.join(expiddir, 'fluxcalib-*{}-{}.fits'.format(spectro, expid)))
            if len(calibfiles) == 3:
                #print(calibfile, os.path.isfile(calibfile))
                col = next(colors)
                for camera in ('b', 'r' , 'z'):
                    cframefile = os.path.join(expiddir, 'cframe-{}{}-{}.fits'.format(camera, spectro, expid))
                    calibfile = os.path.join(expiddir, 'fluxcalib-{}{}-{}.fits'.format(camera, spectro, expid))

                    info = fitsio.FITS(calibfile)
                    hdr = info['FLUXCALIB'].read_header()
                    exptime = hdr['EXPTIME'] * u.s

                    wave = info['WAVELENGTH'].read() * u.angstrom
                    flux = info['FLUXCALIB'].read()
                    ivar = info['IVAR'].read()
                    
                    specthru = flux * 1e17 * (u.electron / u.angstrom) / (u.erg / u.s / u.cm / u.cm / u.angstrom)
                    specthru *= const.h.to(u.erg * u.s) * const.c.to(u.angstrom / u.s) / exptime / area / wave # [electron/photon]

                    # Plot just the standards--
                    fibermap = desispec.io.read_fibermap(cframefile)
                    istd = np.where(isStdStar(fibermap))[0]
                    for ii in istd:
                        if camera == 'b':
                            label = '{}-{}-{}'.format(int(expid), str(spectro), fibermap['FIBER'][ii])
                        else:
                            label = None
                        if np.max(specthru[ii, :].value) > 1:
                            print('Weird star in EXPID={}, camera={}, fiber={}'.format(expid, camera, fibermap['FIBER'][ii]))
                        else:
                            if camera == 'b':
                                nstar += 1
                            ax.plot(wave.value, specthru[ii, :].value, label=label, alpha=0.8, color=col)
     
    for camera in ('b', 'r' , 'z'):
        ax.plot(thru[camera]._wave, thru[camera].thru(thru[camera]._wave), color='k')#, alpha=0.8)
                    
    ax.set_xlabel(r'Wavelength ($\AA$)')
    ax.set_ylabel('Throughput (electron / photon)')
    ax.set_ylim(0, 1)
    ax.set_title('Night {}, N={}'.format(night, nstar))
    ax.legend(loc='upper right', ncol=2, fontsize=12)

    fig.subplots_adjust(left=0.14, bottom=0.16)

    print('Writing {}'.format(pngfile))
    fig.savefig(pngfile)

def qa_redrock(night, verbose=False, overwrite=False):
    """Make some QAplots of the redrock output.

    """
    import matplotlib.pyplot as plt
    from astropy.table import Table

    # Taken from rrplot
    # --------------------------------------------------
    import redrock
    import redrock.templates
    from redrock.external import desi

    templates_path = redrock.templates.find_templates()
    templates = {}
    for el in templates_path:
        t = redrock.templates.Template(filename=el)
        templates[t.full_type] = t
    archetypes = False
    # --------------------------------------------------

    # Pick out some cool spectra and make plots!
    zcatfile = os.path.join(outdir, 'zcatalog-{}.fits'.format(str(night)))
    zcat = Table.read(zcatfile)

    spectrafiles = glob(os.path.join(outdir, 'spectra-64', 'spectra-64-*.fits'))
    for spectrafile in spectrafiles:
        specfile = os.path.basename(spectrafile)
        zbestfile = os.path.join(outdir, 'spectra-64', specfile.replace('spectra-', 'zbest-'))
        rrfile = os.path.join(outdir, 'spectra-64', specfile.replace('spectra-', 'redrock-').replace('.fits', '.h5'))

        zbest = Table(fitsio.read(zbestfile))
        spectra = desispec.io.read_spectra(spectrafile)

        these = np.where(zbest['ZWARN'] == 0)[0]
        targetids = zbest['TARGETID'][these]
        #spectra = spectra.select(targets=targetids)

        zscan, zfit = redrock.results.read_zscan(rrfile)
        for targetid in targetids:
            targets = desi.DistTargetsDESI(spectrafile, targetids=[targetid], coadd=False)._my_data
            pngfile = os.path.join(outdir, 'qa-redrock-{}.png'.format(targetid))
            fig, ax = plt.subplots()
            p = PlotSpec(targets, templates, zscan, zfit, archetypes=archetypes)
            print('Writing {}'.format(pngfile))
            plt.savefig(pngfile)
        pdb.set_trace()
            
        if False:
            # QSOs
            isqso = [stype.strip() == 'QSO' for stype in zbest['SPECTYPE']]
            these = np.where((zbest['ZWARN'] == 0) * isqso)[0]
            if len(these) > 0:
                targetids = zbest['TARGETID'][these]
                if False:
                    if len(these) == 1:
                        targetids = zbest['TARGETID'][these].astype(str)
                    else:
                        targetids = ','.join(zbest['TARGETID'][these].astype(str))
                    cmd = 'rrplot --specfile {spectrafile} --rrfile {rrfile} --targetids {targetids}'
                    cmd = cmd.format(spectrafile=spectrafile, rrfile=rrfile, targetids=targetids)
                    #os.system(cmd)

            zscan, zfit = redrock.results.read_zscan(rrfile)
            for targetid in targetids:
                targets = desi.DistTargetsDESI(spectrafile, targetids=[targetid], coadd=False)._my_data
                p = PlotSpec(targets, templates, zscan, zfit, archetypes=archetypes)
                plt.savefig('junk.png')
                pdb.set_trace()

    # stars and galaxies
    rfaint = 18
    for spectype in ('STAR', 'GALAXY'):
        these = np.where((zcat['ZWARN'] == 0) * (zcat['FLUX_R'] > 1e9 * 10**(-0.4*rfaint)) *
                         (zcat['SPECTYPE'] == spectype))[0]

        pdb.set_trace()
    
    #rrplot --specfile spectra-64/spectra-64-756.fits --rrfile spectra-64/redrock-64-756.h5 --targetids 35186382380993054,611647134688609410

    pdb.set_trace()
    
def main():

    parser = argparse.ArgumentParser(description='Derive flux-calibrated spectra and estimate the throughput.')

    parser.add_argument('-n', '--night', type=int, default=None, nargs='*', help='Night to analyze.')
    parser.add_argument('--update-fibermaps', action='store_true', help='Update fibermap files')
    parser.add_argument('--fluxcalib', action='store_true', help='Perform flux-calibration.')
    parser.add_argument('--process-exposures', action='store_true', help='Process all exposures.')
    parser.add_argument('--group-spectra', action='store_true', help='Group the data into healpixels.')
    parser.add_argument('--redrock', action='store_true', help='Do redshift fitting.')

    parser.add_argument('--qa-stdstars', action='store_true', help='Standard-star QA.')
    parser.add_argument('--qa-throughput', action='store_true', help='Throughput QA.')
    parser.add_argument('-o', '--overwrite', action='store_true', help='Overwrite existing files.')
    parser.add_argument('-v', '--verbose', action='store_true', help='Be verbose.')
    args = parser.parse_args()

    if args.night is None:
        nights = sorted([int(os.path.basename(nn)) for nn in glob(os.path.join(specprod_dir, 'exposures', '2020????'))])
        #print('Hack!')
        #pdb.set_trace()
        #nights = nights[10:]
    else:
        nights = np.atleast_1d(args.night)

    # Update fibermap and frame files.
    if args.update_fibermaps:
        for night in nights:
            update_frame_fibermaps(night, verbose=args.verbose, overwrite=args.overwrite)

    # Fit the standard stars and perform flux-calibration.
    if args.fluxcalib:
        for night in nights:
            fluxcalib(night, verbose=args.verbose, overwrite=args.overwrite)

    if args.process_exposures:
        for night in nights:
            process_exposures(night, verbose=args.verbose, overwrite=args.overwrite)

    if args.group_spectra:
        spectradir = os.path.join(outdir, 'spectra-64')
        for night in nights:
            cmd = 'desi_group_spectra --reduxdir {outdir} --nights {night} --outdir {spectradir}'
            cmd = cmd.format(outdir=outdir, night=str(night), spectradir=spectradir)
            os.system(cmd)

    if args.redrock:
        specdir = os.path.join(outdir, 'spectra-64')
        spectrafiles = glob(os.path.join(specdir, 'spectra-64-*.fits'))
        for spectrafile in spectrafiles:
            zbestfile = os.path.join(outdir, 'spectra-64', os.path.basename(spectrafile).replace('spectra-', 'zbest-'))
            rrfile = os.path.join(outdir, 'spectra-64', os.path.basename(spectrafile).replace('spectra-', 'redrock-').replace('.fits', '.h5'))

            # Just fit bright objects.
            spectra = desispec.io.read_spectra(spectrafile)
            rfaint = 15
            these = np.where(spectra.fibermap['FLUX_R'] > 1e9 * 10**(-0.4*rfaint))[0]
            targetids = ','.join(spectra.fibermap['TARGETID'][these].astype(str))

            # There's a bug when using --allspec...
            cmd = 'rrdesi --output {rrfile} --zbest {zbestfile} --mp 32 --targetids {targetids} {spectrafiles} '
            cmd = cmd.format(rrfile=rrfile, zbestfile=zbestfile, spectrafiles=spectrafile,
                             targetids=targetids)
            os.system(cmd)

        # Merge all the zbest files together.
        zcatfile = os.path.join(outdir, 'zcatalog-{}.fits'.format(str(args.night)))
        cmd = 'desi_zcatalog -i {specdir} -o {zcatfile} --fibermap '
        if args.verbose:
            cmd += '--verbose'
        cmd = cmd.format(specdir=specdir, zcatfile=zcatfile)
        os.system(cmd)

    # QA plots
    if args.qa_stdstars:
        for night in nights:
            qa_stdstars(night, verbose=args.verbose, overwrite=args.overwrite)
            
    if args.qa_throughput:
        for night in nights:
            qa_throughput(night, verbose=args.verbose, overwrite=args.overwrite)

    #if args.qaplots:
    #    for night in nights:
            #qa_redrock(night, verbose=args.verbose, overwrite=args.overwrite)

if __name__ == '__main__':
    main()
