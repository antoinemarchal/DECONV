# -*- coding: utf-8 -*-
import numpy as np
from astropy import units as u
import matplotlib.pyplot as plt
from astropy.io import fits
from astropy import wcs
from astropy.coordinates import SkyCoord
from radio_beam import Beam
import torch
from tqdm import tqdm as tqdm

from deconv.core import DataProcessor, Imager

import marchalib as ml #remove

plt.ion()

if __name__ == '__main__':    
    print("test core deconv")
    #path data
    path_ms = "/priv/avatar/amarchal/MPol-dev/examples/workflow/data/chan950/" #directory of measurement sets
    path_beams = "/priv/avatar/amarchal/MPol-dev/examples/workflow/data/BEAMS/" #directory of primary beams
    path_holography = "/priv/avatar/amarchal/MPol-dev/examples/workflow/data/holography_beams/" #ASKAP 36
    path_sd = "/priv/avatar/amarchal/IMAGING/data/" #path single-dish data
    pathout = "/priv/avatar/amarchal/MPol-dev/examples/workflow/data/" #path where data will be packaged and stored

    #REF WCS INPUT USER
    filename = "/priv/avatar/amarchal/MPol-dev/examples/workflow/img.fits"
    target_header = fits.open(filename)[0].header
    
    #create data processor
    data_processor = DataProcessor(path_ms, path_beams, path_sd, pathout)
    
    #PRE-COMPUTE DATA
    # data_processor.package_ms(filename="NPZ/uvdata_test.npz", select_fraction=1, uvmin=0, uvmax=7000)
    #package data
    # #pre-compute pb and interpolation grids
    # data_processor.compute_pb_and_grid(target_header, fitsname_pb="reproj_pb_deconv.fits", fitsname_grid="grid_interp2.fits") 
    
    #READ DATA
    #read packaged visibilities from "pathout" directory
    # vis_data = data_processor.read_vis(_npz="NPZ/uvdata_rot4.npz", select_fraction=1)
    vis_data = data_processor.read_vis_from_scratch(uvmin=0, uvmax=7000, chunks=1.e7)
    pb, grid = data_processor.read_pb_and_grid(fitsname_pb="reproj_pb_deconv.fits", fitsname_grid="grid_interp2.fits")
    
    # #read single-dish data from "pathout" directory
    # sd, beam_sd = data_processor.read_sd()
    #single-dish data and beam
    fitsname = "GASS_SMC_Jy_regrid.fits"
    hdu_sd = fits.open(path_sd+fitsname)
    hdr_sd = hdu_sd[0].header
    sd = hdu_sd[0].data[950-532]; sd[sd != sd] = 0. #NaN to 0
    #Beam sd
    beam_sd = Beam(hdr_sd["BMIN"]*u.deg, hdr_sd["BMAJ"]*u.deg, 1.e-12*u.deg)
    sd /= (beam_sd.sr).to(u.arcsec**2).value #convert Jy/beam to Jy/arcsec^2

    #____________________________________________________________________________
    #user parameters
    max_its = 20
    lambda_sd = 0#10
    lambda_r = 20
    device = 0 #0 is GPU and "cpu" is CPU
    positivity = False

    if device == 0: print("GPU:", torch.cuda.get_device_name(0))

    #create image processor
    image_processor = Imager(vis_data,      # visibilities
                             pb,            # array of primary beams
                             grid,          # array of interpolation grids
                             sd,            # single dish data in unit of Jy/arcsec^2
                             beam_sd,       # beam of single-dish data in radio_beam format
                             target_header, # header on which to image the data
                             max_its,       # maximum number of iterations
                             lambda_sd,     # hyper-parameter single-dish
                             lambda_r,      # hyper-parameter regularization
                             positivity,    # impose a positivity constaint
                             device)        # device: 0 is GPU; "cpu" is CPU
    #get image
    result = image_processor.process(units="K") #"Jy/arcsec^2" or "K"

    #write on disk
    hdu0 = fits.PrimaryHDU(result, header=target_header)
    hdulist = fits.HDUList([hdu0])
    hdulist.writeto(pathout + "result_deconv.fits", overwrite=True)
    #_____________________________________________________________________________

    stop

    #Open PB file per antenna
    fitsname  = "/priv/avatar/amarchal/MPol-dev/examples/workflow/data/reproj_pb_all_rotated.fits"
    hdu_pb = fits.open(fitsname)
    hdr_pb = hdu_pb[0].header
    pb_all = hdu_pb[0].data    
    pb_mean = np.mean(pb_all,0)
    pb_mean /= np.max(pb_mean)    
    mask = np.where(pb_mean > 0.05, 1, np.nan)

    w_img = ml.wcs2D(target_header)

    #PLOT RESULT
    pathout="/priv/avatar/amarchal/ASKAP/IMAGING/plot/"
    fig = plt.figure(figsize=(10, 10))
    ax = fig.add_axes([0.1,0.1,0.78,0.8], projection=w_img)
    ax.set_xlabel(r"RA (deg)", fontsize=18.)
    ax.set_ylabel(r"DEC (deg)", fontsize=18.)
    img = ax.imshow(result*mask, vmin=-30, vmax=40, origin="lower", cmap="inferno")
    ax.contour(pb_mean, linestyles="--", levels=[0.05, 0.1], colors=["w","w"])
    colorbar_ax = fig.add_axes([0.89, 0.11, 0.02, 0.78])
    cbar = fig.colorbar(img, cax=colorbar_ax)
    cbar.ax.tick_params(labelsize=14.)
    cbar.set_label(r"$T_b$ (K)", fontsize=18.)
    plt.savefig(pathout + 'deconv_SMC_nufftt_ASKAP.png', format='png', bbox_inches='tight', pad_inches=0.02, dpi=400)
    
    #PLOT RESULT
    pathout="/priv/avatar/amarchal/ASKAP/IMAGING/plot/"
    fig = plt.figure(figsize=(10, 10))
    ax = fig.add_axes([0.1,0.1,0.78,0.8], projection=w_img)
    ax.set_xlabel(r"RA (deg)", fontsize=18.)
    ax.set_ylabel(r"DEC (deg)", fontsize=18.)
    img = ax.imshow(result*mask, vmin=-15, vmax=90, origin="lower", cmap="inferno")
    ax.contour(pb_mean, linestyles="--", levels=[0.05, 0.1], colors=["w","w"])
    colorbar_ax = fig.add_axes([0.89, 0.11, 0.02, 0.78])
    cbar = fig.colorbar(img, cax=colorbar_ax)
    cbar.ax.tick_params(labelsize=14.)
    cbar.set_label(r"$T_b$ (K)", fontsize=18.)
    plt.savefig(pathout + 'deconv_SMC_nufftt_ASKAP_Parkes.png', format='png', bbox_inches='tight', pad_inches=0.02, dpi=400)
