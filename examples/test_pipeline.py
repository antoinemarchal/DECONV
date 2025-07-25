# -*- coding: utf-8 -*-
import numpy as np
from astropy import units as u
import matplotlib.pyplot as plt
from astropy.io import fits
from astropy.coordinates import SkyCoord
from radio_beam import Beam
import multiprocessing

from deconv.pipeline import Pipeline
from deconv import logger

plt.ion()

if __name__ == '__main__':
    #path data
    path_ms = "/priv/avatar/amarchal/gaskap/fullsurvey/"#sb69152/"
    
    path_beams = "/priv/avatar/amarchal/Projects/deconv/examples/data/ASKAP/BEAMS/" #directory of primary beams
    path_sd = "/priv/avatar/amarchal/GASS/data/" #path single-dish data - dummy here
    pathout = "/priv/avatar/amarchal/Projects/deconv/examples/data/ASKAP/" #path where data will be packaged and stored

    #REF WCS INPUT USER
    cfield = SkyCoord(ra="1h21m46s", dec="-72d19m26s", frame='icrs')
    filename = "/priv/avatar/amarchal/MPol-dev/examples/workflow/img.fits"
    target_header = fits.open(filename)[0].header
    target_header["CRVAL1"] = cfield.ra.value
    target_header["CRVAL2"] = cfield.dec.value
    target_header["CRPIX1"] = 2500
    target_header["CRPIX2"] = 2500
    target_header["NAXIS2"] = 5000; target_header["NAXIS1"] = 5000
    shape = (target_header["NAXIS2"], target_header["NAXIS1"])    
    
    #____________________________________________________________________________
    # Single dish beam
    beam_sd = Beam((16*u.arcmin).to(u.deg),(16*u.arcmin).to(u.deg), 1.e-12*u.deg) #must be in deg
    # Single dish data
    fitsname = "reproj_GASS_v.fits"
    hdu_sd = fits.open(path_sd+fitsname)
    hdr_sd = hdu_sd[0].header
    sd = hdu_sd[0].data; sd[sd != sd] = 0. #NaN to 0
    sd = sd * beam_sd.jtok((1419773148.148148*u.Hz).to(u.GHz)).value # convertion from K to Jy/beam
    sd /= (beam_sd.sr).to(u.arcsec**2).value #convert Jy/beam to Jy/arcsec^2
    sd /= 2#ASKAP I convention
    
    #____________________________________________________________________________
    # Define separate worker counts
    data_processor_workers = 1   # Workers for DataProcessor
    imager_workers = 1           # Workers for the Imager
    queue_maxsize = 1            # Queue size to balance memory and speed| warning make sure is large
    beam_workers = 1             # Wrokers for beams only if no GPU (not scaling)
    blocks = 'multiple'          # Single or multiple blocks in path_ms
    max_blocks = 1            # Maximum number of blocks used; all if None
    extension = ".ms"
    fixms = False
    precompute = False
    
    # User parameters Imager
    max_its = 25
    lambda_sd = 0
    lambda_r = 10
    device = 0 #0 is GPU and "cpu" is CPU
    positivity = False
    units = "Jy/beam" #"Jy/arcsec^2"
    uvmin = 0                    
    uvmax = np.inf
    write_mode = "live" #or "final"

    # Cube parameters
    start, end, step = 942, 943, 1
    filename = f"result_chan_{start:04d}_to_{end-1:04d}_{step:02d}_Jy_beam_nblocks_1.fits"

    pipeline = Pipeline(
        path_ms=path_ms, path_beams=path_beams, path_sd=path_sd, pathout=pathout,
        filename=filename, target_header=target_header, sd=sd, beam_sd=beam_sd, units=units, max_its=max_its,
        lambda_sd=lambda_sd, lambda_r=lambda_r, positivity=positivity, device=device,
        start=start, end=end, step=step, data_processor_workers=data_processor_workers,
        imager_workers=imager_workers, beam_workers=beam_workers, queue_maxsize=queue_maxsize, uvmin=uvmin, uvmax=uvmax,
        extension=extension, blocks=blocks, max_blocks=max_blocks, fixms=fixms, precompute=precompute, write_mode=write_mode
    )
    
    pipeline.run()
