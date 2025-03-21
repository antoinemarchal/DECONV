# -*- coding: utf-8 -*-
import glob
import numpy as np
from astropy import units as u
import matplotlib.pyplot as plt
from astropy.io import fits
from astropy import wcs
from astropy.coordinates import SkyCoord
from radio_beam import Beam
import torch
from tqdm import tqdm as tqdm
from reproject import reproject_interp
from astropy.constants import c

from deconv.core import DataProcessor, Imager

import marchalib as ml #remove

plt.ion()

if __name__ == '__main__':    
    print("test on MeerKat data from Enrico")
    #path data
    path_ms = "/priv/avatar/amarchal/Projects/deconv/examples/data/MeerKAT/msl_cloud/" #directory of measurement sets
    path_beams = "/priv/avatar/amarchal/Projects/deconv/examples/data/MeerKAT/BEAMS/" #directory of primary beams
    path_sd = "/priv/avatar/amarchal/Projects/deconv/examples/data/MeerKAT/" #path single-dish data
    pathout = "/priv/avatar/amarchal/Projects/deconv/examples/data/MeerKAT/" #path where data will be packaged and stored

    #REF WCS INPUT USER
    filename = "/priv/avatar/amarchal/Projects/deconv/examples/data/MeerKAT/MW-C10_mom0th_NHI.fits"
    target_header = fits.open(filename)[0].header
    
    #create data processor
    data_processor = DataProcessor(path_ms, path_beams, path_sd, pathout)
    
    # PRE-COMPUTE DATA
    # data_processor.package_ms(filename="NPZ/uvdata_rot_Enrico.npz", select_fraction=1, uvmin=0, uvmax=7000) #package data
    # # pre-compute pb and interpolation grids
    # data_processor.compute_pb_and_grid(target_header, fitsname_pb="reproj_pb.fits", fitsname_grid="grid_interp.fits") 
    
    #READ DATA
    #read packaged visibilities from "pathout" directory
    velocity = -155*u.km/u.s
    rest_freq_u = 1.42040575177e9 * u.Hz  # More precise 21 cm HI line rest frequency
    chan_freq = rest_freq_u - (velocity * rest_freq_u) / c

    vis_data = data_processor.read_vis_from_scratch(uvmin=0, uvmax=7000, chunks=1.e7, target_frequency=chan_freq) 

    # vis_data = data_processor.read_vis(_npz="NPZ/uvdata_rot_Enrico.npz", select_fraction=1)
    pb, grid = data_processor.read_pb_and_grid(fitsname_pb="reproj_pb.fits", fitsname_grid="grid_interp.fits")
    
    #read single-dish data from "pathout" directory
    # fitsname = "MW-C10_GBT_MW_-32kms.fits"
    fitsname = "MW-C10_GBT_-155kms.fits"
    hdu_sd = fits.open(path_sd+fitsname)
    hdr_sd = hdu_sd[0].header
    sd = hdu_sd[0].data[0]; sd[sd != sd] = 0. #NaN to 0
    #Beam sd
    beam_sd = Beam(hdr_sd["BMIN"]*u.deg, hdr_sd["BMAJ"]*u.deg, 1.e-12*u.deg)
    sd /= (beam_sd.sr).to(u.arcsec**2).value #convert Jy/beam to Jy/arcsec^2

    #reproject on target_header                                                    
    w_sd = ml.wcs2D(hdr_sd)
    w = ml.wcs2D(target_header)
    shape_out = (target_header["NAXIS2"],target_header["NAXIS1"]) 
    sd, footprint = reproject_interp((sd,w_sd.to_header()), w.to_header(), shape_out)
    sd[sd != sd] = 0.
    
    #____________________________________________________________________________
    #user parameters
    max_its = 20
    lambda_sd = 1#10
    lambda_r = 10
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

    stop
    
    #PLOT RESULT
    fig = plt.figure(figsize=(10, 10))
    ax = fig.add_axes([0.1,0.1,0.78,0.8], projection=w_img)
    ax.set_xlabel(r"RA (deg)", fontsize=18.)
    ax.set_ylabel(r"DEC (deg)", fontsize=18.)
    img = ax.imshow(result*mask, vmin=-10, vmax=20, origin="lower", cmap="viridis")
    ax.contour(pb_mean, linestyles="--", levels=[0.05, 0.1], colors=["w","w"])
    colorbar_ax = fig.add_axes([0.89, 0.11, 0.02, 0.78])
    cbar = fig.colorbar(img, cax=colorbar_ax)
    cbar.ax.tick_params(labelsize=14.)
    cbar.set_label(r"$T_b$ (K)", fontsize=18.)
    plt.savefig(pathout + 'plot/GIF/deconv_result_mw_MeerKAT_{:02d}.png'.format(i), format='png', bbox_inches='tight', pad_inches=0.02, dpi=400)

    #write on disk
    hdu0 = fits.PrimaryHDU(result, header=target_header)
    hdulist = fits.HDUList([hdu0])
    hdulist.writeto(pathout + "result_deconv.fits", overwrite=True)
    #_____________________________________________________________________________

    stop

    #mean pb
    filenames = sorted(glob.glob(path_beams+"*.fits"))
    n_beams = len(filenames)
    pb_all = np.zeros((n_beams,result.shape[0],result.shape[1]))
    w = ml.wcs2D(target_header)
    shape_out = (target_header["NAXIS2"],target_header["NAXIS1"])
    for i in tqdm(np.arange(n_beams)):
        #open beam cube
        hdu_pb = fits.open(filenames[i])
        hdr_pb = hdu_pb[0].header
        pb2 = hdu_pb[0].data
        pb2[pb2 != pb2] = 0.
        shape = (hdr_pb["NAXIS2"],hdr_pb["NAXIS1"])
        w_pb = ml.wcs2D(hdr_pb)
        pb2, footprint = reproject_interp((pb2,w_pb.to_header()), w.to_header(), shape_out)
        pb2[pb2 != pb2] = 0.
        pb_all += pb2
    pb_mean = np.nanmean(pb_all,0)
    pb_mean /= np.nanmax(pb_mean)    
    mask = np.where(pb_mean > 0.05, 1, np.nan)

    w_img = ml.wcs2D(target_header)
    
    #PLOT RESULT
    fig = plt.figure(figsize=(10, 10))
    ax = fig.add_axes([0.1,0.1,0.78,0.8], projection=w_img)
    ax.set_xlabel(r"RA (deg)", fontsize=18.)
    ax.set_ylabel(r"DEC (deg)", fontsize=18.)
    img = ax.imshow(result*mask, vmin=-4, vmax=8, origin="lower", cmap="viridis")
    ax.contour(pb_mean, linestyles="--", levels=[0.05, 0.1], colors=["w","w"])
    colorbar_ax = fig.add_axes([0.89, 0.11, 0.02, 0.78])
    cbar = fig.colorbar(img, cax=colorbar_ax)
    cbar.ax.tick_params(labelsize=14.)
    cbar.set_label(r"$T_b$ (K)", fontsize=18.)
    plt.savefig(pathout + 'deconv_result_cloud_MeerKAT_GBT.png', format='png', bbox_inches='tight', pad_inches=0.02, dpi=400)
