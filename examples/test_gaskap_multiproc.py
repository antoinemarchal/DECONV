# -*- coding: utf-8 -*-
import numpy as np
from astropy import units as u
from astropy.constants import c
import matplotlib.pyplot as plt
from astropy.io import fits
from astropy import wcs
from astropy.coordinates import SkyCoord
from radio_beam import Beam
import torch
from tqdm import tqdm as tqdm
import queue  # Standard Python queue for passing data between threads
import threading
import multiprocessing
import time
from multiprocessing import Array

from deconv.core import DataVisualizer, DataProcessor, Imager
from deconv.utils import dutils
from deconv import logger

import marchalib as ml #remove

plt.ion()

if __name__ == '__main__':    
    #path data
    path_ms = "/priv/avatar/amarchal/gaskap/fullsurvey/"#sb67521/"#sb68329/"
    
    path_beams = "/priv/avatar/amarchal/Projects/deconv/examples/data/ASKAP/BEAMS/" #directory of primary beams
    path_sd = "/priv/avatar/amarchal/GASS/data/" #path single-dish data - dummy here
    pathout = "/priv/avatar/amarchal/Projects/deconv/examples/data/ASKAP/" #path where data will be packaged and stored

    #REF WCS INPUT USER
    cfield = SkyCoord(ra="1h21m46s", dec="-72d19m26s", frame='icrs')
    filename = "/priv/avatar/amarchal/MPol-dev/examples/workflow/img.fits"
    target_header = fits.open(filename)[0].header
    target_header["CRVAL1"] = cfield.ra.value
    target_header["CRVAL2"] = cfield.dec.value

    #create data processor
    data_visualizer = DataVisualizer(path_ms, path_beams, path_sd, pathout)
    data_processor = DataProcessor(path_ms, path_beams, path_sd, pathout)
    
    #PRE-COMPUTE DATA
    #untardir and fixms
    # data_processor.untardir(max_workers=6, clear=False) #warning clean=True will clear the .tar files
    # data_processor.fixms()
    # Continuum subtractin using casatools
    #XXX fixme
    # Compute effective primary beam - not used in imaging
    #XXX fixme put effpb.py in core.py
    #pre-compute pb and interpolation grids
    # data_processor.compute_pb_and_grid(target_header, fitsname_pb="reproj_pb_Dave.fits", fitsname_grid="grid_interp_Dave.fits") 
    
    #READ DATA - WILL BE IGNORED HERE FOR NOW

    # #read single-dish data from "pathout" directory
    # sd, beam_sd = data_processor.read_sd()
    #single-dish data and beam
    fitsname = "reproj_GASS_v.fits"
    hdu_sd = fits.open(path_sd+fitsname)
    hdr_sd = hdu_sd[0].header
    sd = hdu_sd[0].data; sd[sd != sd] = 0. #NaN to 0
    #Beam sd
    # beam_sd = Beam(hdr_sd["BMIN"]*u.deg, hdr_sd["BMAJ"]*u.deg, 1.e-12*u.deg)
    beam_sd = Beam((16*u.arcmin).to(u.deg),(16*u.arcmin).to(u.deg), 1.e-12*u.deg) #must be all in deg
    # sd = sd * beam_sd.jtok(vis_data.frequency*u.GHz).value # convertion from K to Jy/beam
    # sd /= (beam_sd.sr).to(u.arcsec**2).value #convert Jy/beam to Jy/arcsec^2
    # sd /= 2#ASKAP I convention
    
    pb, grid = data_processor.read_pb_and_grid(fitsname_pb="reproj_pb_Dave.fits", fitsname_grid="grid_interp_Dave.fits")

    # Select velocity - will get the closest
    velocity = 200*u.km/u.s
    rest_freq_u = 1.42040575177e9 * u.Hz  # Must be in Hz
    chan_freq = rest_freq_u - (velocity * rest_freq_u) / c

    #____________________________________________________________________________
    #user parameters
    max_its = 20
    lambda_sd = 0#1
    lambda_r = 20
    device = 0#"cpu" #0 is GPU and "cpu" is CPU
    positivity = False

    # Define separate worker counts
    data_processor_workers = 32  # Workers for DataProcessor
    imager_workers = 8           # Workers for the Imager
    queue_maxsize = 12           # Queue size to balance memory and speed
    blocks = 'multiple'          # Single or multiple blocks in path_ms
    uvmin = 0                    
    uvmax = 7000
    extension = ".ms"
        
    # Define cube parameters
    start, end, step = 900, 1050, 1
    idlist = np.arange(start, end, step)
    shape = (target_header["NAXIS2"], target_header["NAXIS1"])

    num_channels = len(idlist)
    num_elements_per_channel = int(np.prod(shape))
    cube_total_size = num_channels * num_elements_per_channel
    
    # Create a shared flat array (double precision) for the cube
    shared_cube = multiprocessing.Array('d', cube_total_size, lock=True)
    
    def preload_visibilities(data_processor, idlist, vis_queue):
        """Loads visibility data while ensuring strict queue space waiting."""
        logger.info("Starting visibility preloading...")
        
        for i in idlist:
            logger.info(f"Waiting for space in queue to load channel {i}...")
            
            # Strictly wait until there is space in the queue
            while vis_queue.full():
                time.sleep(0.1)  # Small wait to prevent busy looping
                
            logger.info(f"Loading visibilities for channel {i}...")
                
            try:
                vis_data = data_processor.read_vis_from_scratch(
                    uvmin=uvmin, uvmax=uvmax, target_frequency=None,
                    target_channel=i, extension=extension, blocks=blocks, max_workers=data_processor_workers
                )
                vis_queue.put((i, vis_data))  # Add visibility data to queue
            except Exception as e:
                logger.error(f"Error loading visibilities for channel {i}: {e}")
                vis_queue.put((i, None))  # Send error signal
                
        logger.info("Finished preloading all visibilities.")
                
        # Send one sentinel value for each worker, so they know when to stop
        for _ in range(imager_workers):
            vis_queue.put(None)
            
    def process_visibilities(vis_queue, shape, pb, grid, sd, beam_sd, target_header, max_its, lambda_sd, lambda_r, positivity, device, cube):
        """Processes visibility data as soon as it's available in the queue."""
        while True:
            item = vis_queue.get()
            if item is None:
                logger.info("Received stop signal. Exiting worker.")
                break  # Exit loop when sentinel value is received
            
            i, vis_data = item
            if vis_data is None:
                logger.error(f"Skipping channel {i} due to failed visibility loading.")
                continue  # Skip failed visibility data
            
            logger.info(f"Processing visibilities for channel {i}...")
            
            init_params = np.zeros(shape).ravel()
            image_processor = Imager(vis_data=vis_data,
                                     pb=pb,
                                     grid=grid,
                                     sd=sd,
                                     beam_sd=beam_sd,
                                     hdr=target_header,
                                     init_params=init_params,
                                     max_its=max_its,
                                     lambda_sd=lambda_sd,
                                     lambda_r=lambda_r,
                                     positivity=positivity,
                                     device=device)
            
            try:
                result = image_processor.process(units="Jy/arcsec^2")
                logger.info(f"Successfully processed channel {i}. Storing result in shared_cube.")

                # Compute the correct offset into the flat array:
                channel_index = i - start
                offset = channel_index * num_elements_per_channel
                flat_result = result.ravel()  # Ensure the result is flat
                
                # Write the flat_result into the shared_cube (with locking)
                with shared_cube.get_lock():
                    for j in range(num_elements_per_channel):
                        shared_cube[offset + j] = flat_result[j]
            except Exception as e:
                logger.error(f"Error processing channel {i}: {e}")
                

    # Create a queue with a strict max size
    vis_queue = multiprocessing.Queue(maxsize=queue_maxsize)
    
    # Start the preloading process
    preload_process = multiprocessing.Process(target=preload_visibilities,
                                              args=(data_processor, idlist, vis_queue))
    preload_process.start()
    
    # Start parallel processing workers
    processing_workers = []
    for _ in range(imager_workers):
        worker = multiprocessing.Process(
            target=process_visibilities,
            args=(vis_queue, shape, pb, grid, sd, beam_sd, target_header,
                  max_its, lambda_sd, lambda_r, positivity, device, shared_cube)
        )
        worker.start()
        processing_workers.append(worker)
        
    # Ensure preloading completes
    preload_process.join()
    
    # Ensure all processing workers complete
    for worker in processing_workers:
        worker.join()
        
    logger.info("Processing completed successfully.")
    
    # Convert shared_cube (flat) to a NumPy array and reshape it to the cube dimensions:
    final_cube = np.frombuffer(shared_cube.get_obj()).reshape((num_channels, *shape))
        
    #write on disk
    filename = f"result_chan_{start:04d}_to_{end-1:04d}_{step:02d}_Jy_arcsec2.fits"
    hdu0 = fits.PrimaryHDU(final_cube, header=target_header)
    hdulist = fits.HDUList([hdu0])
    hdulist.writeto(pathout + filename, overwrite=True)
    
    # stop

    # #Open PB file per antenna
    # hdu_pb = fits.open(pathout+"effpb.fits")
    # hdr_pb = hdu_pb[0].header
    # effpb = hdu_pb[0].data    
    # effpb /= np.max(effpb)    
    # mask = np.where(effpb > 0.05, 1, np.nan)

    # w_img = ml.wcs2D(target_header)

    # #PLOT RESULT
    # fig = plt.figure(figsize=(10, 10))
    # ax = fig.add_axes([0.1,0.1,0.78,0.8], projection=w_img)
    # ax.set_xlabel(r"RA (deg)", fontsize=18.)
    # ax.set_ylabel(r"DEC (deg)", fontsize=18.)
    # img = ax.imshow(result*mask, vmin=-30, vmax=40, origin="lower", cmap="inferno")
    # ax.contour(effpb, linestyles="--", levels=[0.05, 0.1], colors=["w","w"])
    # colorbar_ax = fig.add_axes([0.89, 0.11, 0.02, 0.78])
    # cbar = fig.colorbar(img, cax=colorbar_ax)
    # cbar.ax.tick_params(labelsize=14.)
    # cbar.set_label(r"$T_b$ (K)", fontsize=18.)
    # # plt.title("scienceData.MS_M345-09A_1")
    # plt.savefig(pathout + 'plot/deconv_SMC_ASKAP_Dave.png', format='png', bbox_inches='tight', pad_inches=0.02, dpi=400)
    
    # #PLOT RESULT
    # pathout="/priv/avatar/amarchal/ASKAP/IMAGING/plot/"
    # fig = plt.figure(figsize=(10, 10))
    # ax = fig.add_axes([0.1,0.1,0.78,0.8], projection=w_img)
    # ax.set_xlabel(r"RA (deg)", fontsize=18.)
    # ax.set_ylabel(r"DEC (deg)", fontsize=18.)
    # img = ax.imshow(result*mask*2.0285478975236693, vmin=-15, vmax=90, origin="lower", cmap="inferno") #FIME norm tmp
    # ax.contour(effpb, linestyles="--", levels=[0.05, 0.1], colors=["w","w"])
    # colorbar_ax = fig.add_axes([0.89, 0.11, 0.02, 0.78])
    # cbar = fig.colorbar(img, cax=colorbar_ax)
    # cbar.ax.tick_params(labelsize=14.)
    # cbar.set_label(r"$T_b$ (K)", fontsize=18.)
    # plt.savefig(pathout + 'deconv_SMC_nufftt_ASKAP_Parkes.png', format='png', bbox_inches='tight', pad_inches=0.02, dpi=400)


# #Get CHIPASS
# hdu_chipass = fits.open(pathout+"reproj_CHIPASS.fits")    
# sd += (hdu_chipass[0].data *1.e-3)

    # # Create a multiprocessing queue (size 1: strictly one preloaded batch at a time)
    # vis_queue = multiprocessing.Queue(maxsize=1)
    
    # def preload_visibilities(data_processor, idlist, vis_queue):
    #     """Loads visibility data while ensuring only one is preloaded at a time."""
    #     logger.info("Starting visibility preloading...")
        
    #     for i in idlist:
    #         logger.info(f"Loading visibilities for channel {i}...")
            
    #         # Ensure only ONE preloaded batch is in memory
    #         while vis_queue.full():
    #             time.sleep(0.1)  # Wait for processing to consume an item
                
    #         try:
    #             vis_data = data_processor.read_vis_from_scratch(
    #                 uvmin=0, uvmax=7000, target_frequency=None, 
    #                 target_channel=i, extension=".ms", blocks='multiple', max_workers=12
    #             )
                
    #             vis_queue.put((i, vis_data))  # Store (index, data) in queue
    #             logger.info(f"Preloaded channel {i}, waiting for processing...")
    #         except Exception as e:
    #             logger.error(f"Error loading visibilities for channel {i}: {e}")
    #             vis_queue.put(None)  # Signal an error
                
    #     vis_queue.put(None)  # Sentinel value to signal end of processing
    #     logger.info("Finished preloading all visibilities.")
                
    # # Start the preloading process
    # preload_process = multiprocessing.Process(target=preload_visibilities, args=(data_processor, idlist, vis_queue))
    # preload_process.start()
    
    # # Processing loop
    # for k in range(len(idlist)):
    #     logger.info(f"Waiting for preloaded visibilities (iteration {k})...")
        
    #     item = vis_queue.get()  # Fetch preloaded visibilities
    #     if item is None:  # If sentinel value received, exit loop
    #         logger.warning("No more visibilities to process. Exiting loop.")
    #         break
        
    #     i, vis_data = item
    #     logger.info(f"Processing visibilities for channel {i}...")
        
    #     # Set initial parameters
    #     init_params = np.zeros(shape).ravel()
        
    #     # Create image processor
    #     image_processor = Imager(vis_data, pb, grid, sd, beam_sd, target_header, 
    #                              init_params, max_its, lambda_sd, lambda_r, positivity, device)
        
    #     # Start processing
    #     try:
    #         result = image_processor.process(units="Jy/arcsec^2")  
    #         logger.info(f"Successfully processed channel {i}.")
    #     except Exception as e:
    #         logger.error(f"Error processing channel {i}: {e}")
    #         break
        
    #     # Store result in cube
    #     cube[k] = result  
        
    # # Ensure preloading is completed
    # logger.info("Joining the preload process...")
    # preload_process.join()
    # logger.info("Processing completed successfully.")    
