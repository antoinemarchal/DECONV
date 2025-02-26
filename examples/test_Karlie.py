# -*- coding: utf-8 -*-
import glob
import os
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
from spectral_cube import SpectralCube

from deconv.core import DataVisualizer, DataProcessor, Imager

import marchalib as ml #remove

plt.ion()

def wcs2D(hdr):
    w = wcs.WCS(naxis=2)
    w.wcs.crpix = [hdr['CRPIX1'], hdr['CRPIX2']]
    w.wcs.cdelt = np.array([hdr['CDELT1'], hdr['CDELT2']])
    w.wcs.crval = [hdr['CRVAL1'], hdr['CRVAL2']]
    w.wcs.ctype = [hdr['CTYPE1'], hdr['CTYPE2']]
    return w

if __name__ == '__main__':    
    #path data
    # path_ms = "/priv/myrtle1/gaskap/karlie/meerkat2024/data/fields/original_ms/contsub/split/" #directory of measurement sets
    path_ms = "/home/amarchal/Projects/deconv/examples/data/MeerKAT/original/" #directory of measurement sets    
    path_beams = "/priv/avatar/amarchal/Projects/deconv/examples/data/MeerKAT/BEAMS/" #directory of primary beams
    path_sd = "/priv/myrtle1/gaskap/karlie/meerkat2024/data/fields/original_ms/" #path single-dish data
    pathout = "/priv/avatar/amarchal/Projects/deconv/examples/data/MeerKAT/" #path where data will be packaged and stored
    
    #REF WCS INPUT USER
    filename = "/priv/avatar/amarchal/Projects/deconv/examples/data/MeerKAT/MW-C10_mom0th_NHI.fits"
    target_header = fits.open(filename)[0].header
    
    #create data processor
    data_visualizer = DataVisualizer(path_ms, path_beams, path_sd, pathout)
    data_processor = DataProcessor(path_ms, path_beams, path_sd, pathout)
    
    # Test DataVisualizer
    # data_visualizer.msplot(0) # Plot I vs channel and velocity for ant2&3 for the first ms in directory

    # Select velocity - will get the closest
    velocity = -43*u.km/u.s#-182*u.km/u.s
    rest_freq_u = 1.42040575177e9 * u.Hz  # Must be in Hz
    chan_freq = rest_freq_u - (velocity * rest_freq_u) / c

    # Test read data
    # vis_data = data_processor.read_vis_from_scratch(uvmin=0, uvmax=7000, chunks=1.e6,
    #                                                 target_frequency=None,
    #                                                 target_channel=788,
    #                                                 extension=".contsub") #fixme dummy chunks

    # # pre-compute pb and interpolation grids
    # data_processor.compute_pb_and_grid(target_header, fitsname_pb="reproj_pb_test.fits", fitsname_grid="grid_interp_test.fits") 
    pb, grid = data_processor.read_pb_and_grid(fitsname_pb="reproj_pb.fits", fitsname_grid="grid_interp.fits")

    #Open SD data
    fitsname="/home/amarchal/Projects/deconv/examples/data/MeerKAT/MW_C10_GBT_0.7kms.fits"
    hdu_sd = fits.open(fitsname)
    hdr_sd = hdu_sd[0].header
    w_sd = wcs2D(hdr_sd)
    # Load the sd data cube (downloaded earlier)
    cube_sd = SpectralCube.read(fitsname)
        
    #Beam sd
    beam_sd = Beam(hdr_sd["BMIN"]*u.deg, hdr_sd["BMAJ"]*u.deg, 1.e-12*u.deg)

    #____________________________________________________________________________
    #user parameters
    max_its = 25
    lambda_sd = 1#5
    lambda_r = 10
    device = 0#"cpu" #0 is GPU and "cpu" is CPU
    positivity = False
    
    #BUILD CUBE
    N = 300; START=0
    cube = np.zeros((N,target_header["NAXIS2"],target_header["NAXIS1"]))
    
    for i in np.arange(N):        
        #Read data
        vis_data = data_processor.read_vis_from_scratch(uvmin=0, uvmax=np.inf,
                                                        target_frequency=None,
                                                        target_channel=START+i,
                                                        extension=".vlsrk") #fixme dummy chunks

        # Define the velocity you want
        target_velocity = vis_data.velocity.value  # Correct way to create a Quantity
        
        # Interpolate the HI intensity at the exact velocity
        hi_slice = cube_sd.spectral_interpolate(np.array([target_velocity])*u.km/u.s)
        hi_slice_array = hi_slice.hdu.data[0]  # Convert the SpectralCube slice to a NumPy array
        shape = (target_header["NAXIS2"],target_header["NAXIS1"])
        
        #reproject on target_header
        w = wcs2D(target_header)
        target_hdr = w.to_header()
        sd, footprint = reproject_interp((hi_slice_array,w_sd.to_header()), target_hdr, shape_out=(shape[0],shape[1]))
        sd[sd != sd] = 0.        
        
        sd /= (beam_sd.sr).to(u.arcsec**2).value #convert Jy/beam to Jy/arcsec^2
        
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
        # Move to cube
        cube[i] = result

    #write on disk
    filename = f"result_chan_{START:04d}_to_{START+N:04d}.fits"
    hdu0 = fits.PrimaryHDU(cube, header=target_header)
    hdulist = fits.HDUList([hdu0])
    hdulist.writeto(pathout + filename, overwrite=True)


    stop
    
    #PLOT RESULT
    fig = plt.figure(figsize=(10, 10))
    ax = fig.add_axes([0.1,0.1,0.78,0.8], projection=w_img)
    ax.set_xlabel(r"RA (deg)", fontsize=18.)
    ax.set_ylabel(r"DEC (deg)", fontsize=18.)
    img = ax.imshow(np.sum(cube,0)*mask, vmin=-30, vmax=50, origin="lower", cmap="gray_r")
    ax.contour(pb_mean, linestyles="--", levels=[0.05, 0.1], colors=["w","w"])
    colorbar_ax = fig.add_axes([0.89, 0.11, 0.02, 0.78])
    cbar = fig.colorbar(img, cax=colorbar_ax)
    cbar.ax.tick_params(labelsize=14.)
    cbar.set_label(r"$T_b$ (K)", fontsize=18.)
    # plt.savefig(pathout + 'plot/GIF/deconv_result_mw_MeerKAT_{:02d}.png'.format(i), format='png', bbox_inches='tight', pad_inches=0.02, dpi=400)
    plt.savefig(pathout + 'plot/GIF/deconv_result_cloud_MeerKAT.png', format='png', bbox_inches='tight', pad_inches=0.02, dpi=400)

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
