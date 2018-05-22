'''
Created on 29 Nov. 2017

@author: christoph
'''

import numpy as np
from scipy import signal
import matplotlib.pyplot as plt
import scipy.optimize as op
from scipy import ndimage
import lmfit
import warnings
from lmfit import parameter, minimizer, Model
from lmfit.models import LinearModel, GaussianModel
import time
import datetime
from astropy.io import ascii
from astropy.modeling import models, fitting
from mpl_toolkits import mplot3d

from veloce_reduction.helper_functions import *
from readcol import *


# thardata = np.load('/Users/christoph/UNSW/rvtest/thardata.npy').item()
# laserdata = np.load('/Users/christoph/UNSW/rvtest/laserdata.npy').item()
# thdata = thardata['flux']['order_01']
# ldata = laserdata['flux']['order_01']



def find_suitable_peaks(data, thresh = 5000., bgthresh = 2000., gauss_filter_sigma=1., maxthresh = None, debug_level=0, return_masks=False, timit=False):
    
    #this routine is extremely fast, no need to optimise for speed
    if timit:
        start_time = time.time()
    
    xx = np.arange(len(data))
    
    #smooth data to make sure we are not finding noise peaks, and add tiny slope to make sure peaks are found even when pixel-values are like [...,3,6,18,41,41,21,11,4,...]
    filtered_data = ndimage.gaussian_filter(data.astype(np.float), gauss_filter_sigma) + xx*1e-4
    
    #find all local maxima in smoothed data (and exclude the leftmost and rightmost maxima to avoid nasty edge effects...)
    allpeaks = signal.argrelextrema(filtered_data, np.greater)[0]
    
    ### this alternative version of finding extrema is completely equivalent (except that the below version accepts first and last as extrema, which I do not want)
    #testix = np.r_[True, filtered_data[1:] > filtered_data[:-1]] & np.r_[filtered_data[:-1] > filtered_data[1:], True]
    #testpeaks = np.arange(len(xx))[testix]
    
    #exclude first and last peaks to avoid nasty edge effects
    allpeaks = allpeaks[1:-1]
    
    #make first mask to determine which peaks from linelist to use in wavelength solution
    first_mask = np.ones(len(allpeaks), dtype='bool')
    
    #remove shallow noise peaks
    first_mask[filtered_data[allpeaks] < bgthresh] = False
    mostpeaks = allpeaks[first_mask]
    
    #make mask which we need later to determine which peaks from linelist to use in wavelength solution
    second_mask = np.ones(len(mostpeaks), dtype='bool')
    
    #remove saturated lines
    if maxthresh is not None:
        second_mask[filtered_data[mostpeaks] > maxthresh] = False
        mostpeaks = mostpeaks[second_mask]
     
    #make mask which we need later to determine which peaks from linelist to use in wavelength solution    
    third_mask = np.ones(len(mostpeaks), dtype='bool')
    
    #only select good peaks higher than a certain threshold
    third_mask[filtered_data[mostpeaks] < thresh] = False
    goodpeaks = mostpeaks[third_mask]           #ie goodpeaks = allpeaks[first_mask][second_mask][third_mask]
    
    #for testing and debugging...
    if debug_level >= 1:
        print('Total number of peaks found: '+str(len(allpeaks)))
        print('Number of peaks found that are higher than '+str(int(thresh))+' counts: '+str(len(goodpeaks)))
        plt.figure()
        plt.plot(data)
        plt.plot(filtered_data)
        plt.scatter(goodpeaks, data[goodpeaks], marker='x', color='r', s=40)
        plt.plot((0,len(data)),(bgthresh,bgthresh),'r--')
        plt.plot((0,len(data)),(thresh,thresh),'g--')
        #plt.vlines(thar_pos_guess, 0, np.max(data))
        plt.show()
    
    if timit:
        delta_t = time.time() - start_time
        print('Time elapsed: '+str(round(delta_t,5))+' seconds')
    
    if return_masks:
        return goodpeaks, mostpeaks, allpeaks, first_mask, second_mask, third_mask
    else:
        return goodpeaks, mostpeaks, allpeaks



def fit_emission_lines(data, fitwidth=4, thresh = 5000., bgthresh = 2000., maxthresh = None, laser=False, varbeta=True, timit=False, verbose=False, return_all_pars=False, return_qualflag=False):
    
    if timit:
        start_time = time.time()
    
    xx = np.arange(len(data))
    
    #find rough peak locations
    goodpeaks,mostpeaks,allpeaks = find_suitable_peaks(data, thresh=thresh, bgthresh=bgthresh, maxthresh=maxthresh)    
    
    if verbose:
        print('Fitting '+str(len(goodpeaks))+' emission lines...')
    
    line_pos_fitted = []
    if return_all_pars:
        line_amp_fitted = []
        line_sigma_fitted = []
        if varbeta:
            line_beta_fitted = []
    if return_qualflag:
        qualflag = []
        
    for xguess in goodpeaks:
#         if verbose:
#             print('xguess = ',xguess)
        ################################################################################################################################################################################
        #METHOD 1 (using curve_fit; slightly faster than method 2, but IDK how to make sure the fit converged (as with .ier below))
        
        if not laser:
            #check if there are any other peaks in the vicinity of the peak in question (exclude the peak itself)
            checkrange = np.r_[xx[xguess - 2*fitwidth : xguess], xx[xguess+1 : xguess + 2*fitwidth+1]]
            peaks = np.r_[xguess]
            #while len((set(checkrange) & set(allpeaks))) > 0:    THE RESULTS ARE COMPARABLE, BUT USING MOSTPEAKS IS MUCH FASTER
            while len((set(checkrange) & set(mostpeaks))) > 0:
                #where are the other peaks?
                #other_peaks = np.intersect1d(checkrange, allpeaks)    THE RESULTS ARE COMPARABLE, BUT USING MOSTPEAKS IS MUCH FASTER
                other_peaks = np.intersect1d(checkrange, mostpeaks)
                peaks = np.sort(np.r_[peaks, other_peaks])
                #define new checkrange
                checkrange = xx[peaks[0] - 2*fitwidth : peaks[-1] + 2*fitwidth + 1]
                dum = np.in1d(checkrange, peaks)
                checkrange = checkrange[~dum]      
        else:
            peaks = np.r_[xguess]
            
        npeaks = len(peaks)        
        xrange = xx[peaks[0] - fitwidth : peaks[-1] + fitwidth + 1]      #this should satisfy: len(xrange) == len(checkrange) - 2*fitwidth + len(peaks)
        
        if npeaks == 1:
            if varbeta:
                guess = np.array([xguess, 1., data[xguess], 2.])
                popt, pcov = op.curve_fit(fibmodel_with_amp, xrange, data[xrange], p0=guess, bounds=([xguess-2,0,0,1],[xguess+2,np.inf,np.inf,4]))
            else:
                guess = np.array([xguess, 1., data[xguess]])
                popt, pcov = op.curve_fit(CMB_pure_gaussian, xrange, data[xrange], p0=guess, bounds=([xguess-2,0,0],[xguess+2,np.inf,np.inf]))
            fitted_pos = popt[0]
            if return_all_pars:
                fitted_sigma = popt[1]
                fitted_amp = popt[2]
                if varbeta:
                    fitted_beta = popt[3]
        else:
            guess = []
            lower_bounds = []
            upper_bounds = []
            for i in range(npeaks):
                if varbeta:
                    guess.append(np.array([peaks[i], 1., data[peaks[i]], 2.]))
                    lower_bounds.append([peaks[i]-2,0,0,1])
                    upper_bounds.append([peaks[i]+2,np.inf,np.inf,4])
                else:
                    guess.append(np.array([peaks[i], 1., data[peaks[i]]]))
                    lower_bounds.append([peaks[i]-2,0,0])
                    upper_bounds.append([peaks[i]+2,np.inf,np.inf])
            guess = np.array(guess).flatten()
            lower_bounds = np.array(lower_bounds).flatten()
            upper_bounds = np.array(upper_bounds).flatten()
            if varbeta:
                popt, pcov = op.curve_fit(multi_fibmodel_with_amp, xrange, data[xrange], p0=guess, bounds=(lower_bounds,upper_bounds))
            else:
                popt, pcov = op.curve_fit(CMB_multi_gaussian, xrange, data[xrange], p0=guess, bounds=(lower_bounds,upper_bounds))           
            
            #now figure out which peak is the one we wanted originally
            q = np.argwhere(peaks==xguess)[0]
            if varbeta:
                fitted_pos = popt[q*4]
                if return_all_pars:
                    fitted_sigma = popt[q*4+1]
                    fitted_amp = popt[q*4+2]
                    fitted_beta = popt[q*4+3]
            else:
                fitted_pos = popt[q*3]
                if return_all_pars:
                    fitted_sigma = popt[q*3+1]
                    fitted_amp = popt[q*3+2]
                
        
        
        #make sure we actually found a good peak
        if abs(fitted_pos - xguess) >= 2.:
            line_pos_fitted.append(xguess)
            if return_qualflag:
                qualflag.append(0)
            if return_all_pars:
                line_sigma_fitted.append(fitted_sigma)
                line_amp_fitted.append(fitted_amp)
                if varbeta:
                    line_beta_fitted.append(fitted_beta)
        else:
            line_pos_fitted.append(fitted_pos)
            if return_qualflag:
                qualflag.append(1)
            if return_all_pars:
                line_sigma_fitted.append(fitted_sigma)
                line_amp_fitted.append(fitted_amp)
                if varbeta:
                    line_beta_fitted.append(fitted_beta)
        ################################################################################################################################################################################
        
#         ################################################################################################################################################################################
#         #METHOD 2 (using lmfit) (NOTE THAT THE TWO METHODS HAVE different amplitudes for the Gaussian b/c of different normalization, but we are only interested in the position)
#         #xguess = int(xguess)
#         gm = GaussianModel()
#         gm_pars = gm.guess(data[xguess - fitwidth:xguess + fitwidth], xx[xguess - fitwidth:xguess + fitwidth])
#         gm_fit_result = gm.fit(data[xguess - fitwidth:xguess + fitwidth], gm_pars, x=xx[xguess - fitwidth:xguess + fitwidth])
#         
#         #make sure we actually found the correct peak
#         if gm_fit_result.ier not in (1,2,3,4):     #if this is any other value it means the fit did not converge
#         #if gm_fit_result.ier > 4:   
#             # gm_fit_result.plot()
#             # plt.show()
#             thar_pos_fitted.append(xguess)
#         elif abs(gm_fit_result.best_values['center'] - xguess) > 2.:
#             thar_pos_fitted.append(xguess)
#         else:
#             thar_pos_fitted.append(gm_fit_result.best_values['center'])
#         ################################################################################################################################################################################
    
    if verbose:    
        plt.figure()
        plt.plot(xx,data)
        #plt.vlines(thar_pos_guess, 0, np.max(data))
        plt.vlines(line_pos_fitted, 0, np.max(data) * 1.2, color='g', linestyles='dotted')
        plt.show()
    
    if timit:
        print('Time taken for fitting emission lines: '+str(time.time() - start_time)+' seconds...')
    
    if return_all_pars:
        if varbeta:
            if return_qualflag:
                return np.array(line_pos_fitted), np.array(line_sigma_fitted), np.array(line_amp_fitted), np.array(line_beta_fitted), np.array(qualflag)
            else:
                return np.array(line_pos_fitted), np.array(line_sigma_fitted), np.array(line_amp_fitted), np.array(line_beta_fitted)
        else:
            if return_qualflag:
                return np.array(line_pos_fitted), np.array(line_sigma_fitted), np.array(line_amp_fitted), np.array(qualflag)
            else:
                return np.array(line_pos_fitted), np.array(line_sigma_fitted), np.array(line_amp_fitted)
    else:
        if return_qualflag:
            return np.array(line_pos_fitted), np.array(qualflag)
        else:
            return np.array(line_pos_fitted)



###########################################

# ###########################################
# thar_refwlord01, thar_relintord01, flag = readcol('/Users/christoph/UNSW/linelists/test_thar_list_order_01.dat',fsep=';',twod=False)
# thar_refwlord01 *= 1e3
# refdata = {}
# refdata['order_01'] = {}
# refdata['order_01']['wl'] = thar_refwlord01[np.argwhere(flag == ' resolved')][::-1]          #note the array is turned around to match other arrays
# refdata['order_01']['relint'] = thar_relintord01[np.argwhere(flag == ' resolved')][::-1]     #note the array is turned around to match other arrays
# ###########################################



def get_dispsol_from_thar(thardata, refdata, deg_polynomial=5, timit=False, verbose=False):

    if timit:
        start_time = time.time()

    thar_dispsol = {}
    
    #loop over all orders
    #for ord in sorted(thardata['flux'].iterkeys()):
    for ord in ['order_01']:
    
        if verbose:
            print('Finding wavelength solution for '+str(ord))
    
        #find fitted x-positions of ThAr peaks
        fitted_thar_pos, thar_qualflag = fit_emission_lines(thardata['flux'][ord], return_all_pars=False, return_qualflag=True, varbeta=False)
        x = fitted_thar_pos.copy()
        
        #these are the theoretical wavelengths from the NIST linelists
        lam = (refdata[ord]['wl']).flatten()
        
        #exclude some peaks as they are a blend of multiple lines: TODO: clean up
        filt = np.ones(len(fitted_thar_pos),dtype='bool')
        filt[[33,40,42,58,60]] = False
        x = x[filt]
        
        #fit polynomial to lambda as a function of x
        thar_fit = np.poly1d(np.polyfit(x, lam, deg_polynomial))
        #save to output dictionary
        thar_dispsol[ord] = thar_fit

    if timit:
        print('Time taken for finding ThAr wavelength solution: '+str(time.time() - start_time)+' seconds...')

    return thar_dispsol


# '''xxx'''
# #################################################################
# # the following is needed as input for "get_dispsol_from_laser" #
# #################################################################
# laser_ref_wl,laser_relint = readcol('/Users/christoph/UNSW/linelists/laser_linelist_25GHz.dat',fsep=';',twod=False)
# laser_ref_wl *= 1e3
# 
# #wavelength solution from HDF file
# #read dispersion solution from file
# dispsol = np.load('/Users/christoph/UNSW/dispsol/mean_dispsol_by_orders_from_zemax.npy').item()
# #read extracted spectrum from files (obviously this needs to be improved)
# xx = np.arange(4096)
# #this is so as to match the order number with the physical order number (66 <= m <= 108)
# # order01 corresponds to m=66
# # order43 corresponds to m=108
# wl = {}
# for ord in dispsol.keys():
#     m = ord[5:]
#     ordnum = str(int(m)-65).zfill(2)
#     wl['order_'+ordnum] = dispsol['order'+m]['model'](xx)
    


def get_dispsol_from_laser(laserdata, laser_ref_wl, deg_polynomial=5, timit=False, verbose=False, return_stats=False, varbeta=False):
    
    if timit:
        start_time = time.time()

    if return_stats:
        stats = {}

    #read in mask for fibre_01 (ie the Laser-comb fibre) from order_tracing as a first step in excluding low-flux regions
    mask_01 = np.load('/Users/christoph/UNSW/fibre_profiles/masks/mask_01.npy').item()

    laser_dispsol = {}
    
    #loop over all orders
    #order 43 does not work properly, as some laser peaks are missing!!!
    for ord in sorted(laserdata['flux'].iterkeys())[:-1]:

        if verbose:
            print('Finding wavelength solution for '+str(ord))
        
        #find fitted x-positions of ThAr peaks
        data = laserdata['flux'][ord] * mask_01[ord]
        goodpeaks,mostpeaks,allpeaks,first_mask,second_mask,third_mask = find_suitable_peaks(data,return_masks=True)    #from this we just want the masks this time (should be very fast)
        #fitted_laser_pos, laser_qualflag = fit_emission_lines(data, laser=True, return_all_pars=False, return_qualflag=True, varbeta=varbeta)
        if varbeta:
            fitted_laser_pos, fitted_laser_sigma, fitted_laser_amp, fitted_laser_beta = fit_emission_lines(data, laser=True, return_all_pars=True, return_qualflag=False, varbeta=varbeta, timit=timit, verbose=verbose)
        else:
            fitted_laser_pos, fitted_laser_sigma, fitted_laser_amp = fit_emission_lines(data, laser=True, return_all_pars=True, return_qualflag=False, varbeta=varbeta, timit=timit, verbose=verbose)
        x = fitted_laser_pos.copy()
        #exclude the leftmost and rightmost peaks (nasty edge effects...)
#         blue_cutoff = int(np.round((x[-1]+x[-2])/2.,0))
#         red_cutoff = int(np.round((x[0]+x[1])/2.,0))
        blue_cutoff = int(np.round(allpeaks[-1]+((allpeaks[-1] - allpeaks[-2])/2),0))
        red_cutoff = int(np.round(allpeaks[0]-((allpeaks[1] - allpeaks[0])/2),0))
        cond1 = (laser_ref_wl >= wl[ord][blue_cutoff])
        cond2 = (laser_ref_wl <= wl[ord][red_cutoff])
        #these are the theoretical wavelengths from the NIST linelists
        lam = laser_ref_wl[np.logical_and(cond1,cond2)][::-1]
        lam = lam[first_mask][second_mask][third_mask]
        
        #check if the number of lines found equals the number of lines from the line list
#         if verbose:
#             print(len(x),len(lam))
        if len(x) != len(lam):
            print('fuganda')
            return 'fuganda'
        
        #fit polynomial to lambda as a function of x
        laser_fit = np.poly1d(np.polyfit(x, lam, deg_polynomial))
        
        if return_stats:
            stats[ord] = {}
            resid = laser_fit(x) - lam
            stats[ord]['resids'] = resid
            #mean error in RV for a single line = c * (stddev(resid) / mean(lambda))
            stats[ord]['single_rverr'] = 3e8 * (np.std(resid) / np.mean(lam))
            stats[ord]['rverr'] = 3e8 * (np.std(resid) / np.mean(lam)) / np.sqrt(len(lam))
            stats[ord]['n_lines'] = len(lam)
            
        #save to output dictionary
        laser_dispsol[ord] = laser_fit

    
    #let's do order 43 differently because it has the stupid gap in the middle
    #find fitted x-positions of ThAr peaks
    ord = 'order_43'
    if verbose:
            print('Finding wavelength solution for '+str(ord))
    data = laserdata['flux'][ord] * mask_01[ord]
    data1 = data[:2500]
    data2 = data[2500:]
    goodpeaks1,mostpeaks1,allpeaks1,first_mask1,second_mask1,third_mask1 = find_suitable_peaks(data1,return_masks=True)    #from this we just want use_mask this time (should be very fast)
    goodpeaks2,mostpeaks2,allpeaks2,first_mask2,second_mask2,third_mask2 = find_suitable_peaks(data2,return_masks=True)    #from this we just want use_mask this time (should be very fast)
    #fitted_laser_pos1, laser_qualflag1 = fit_emission_lines(data1, laser=True, return_all_pars=False, return_qualflag=True, varbeta=varbeta)
    #fitted_laser_pos2, laser_qualflag2 = fit_emission_lines(data2, laser=True, return_all_pars=False, return_qualflag=True, varbeta=varbeta)
    if varbeta:
        fitted_laser_pos1, fitted_laser_sigma1, fitted_laser_amp1, fitted_laser_beta1 = fit_emission_lines(data1, laser=True, return_all_pars=True, return_qualflag=False, varbeta=varbeta)
        fitted_laser_pos2, fitted_laser_sigma2, fitted_laser_amp2, fitted_laser_beta2 = fit_emission_lines(data2, laser=True, return_all_pars=True, return_qualflag=False, varbeta=varbeta)
    else:
        fitted_laser_pos1, fitted_laser_sigma1, fitted_laser_amp1 = fit_emission_lines(data1, laser=True, return_all_pars=True, return_qualflag=False, varbeta=varbeta)
        fitted_laser_pos2, fitted_laser_sigma2, fitted_laser_amp2 = fit_emission_lines(data2, laser=True, return_all_pars=True, return_qualflag=False, varbeta=varbeta)
    x1 = fitted_laser_pos1.copy()
    x2 = fitted_laser_pos2.copy() + 2500
    #exclude the leftmost and rightmost peaks (nasty edge effects...)
#         blue_cutoff = int(np.round((x[-1]+x[-2])/2.,0))
#         red_cutoff = int(np.round((x[0]+x[1])/2.,0))
    blue_cutoff1 = int(np.round(allpeaks1[-1]+((allpeaks1[-1] - allpeaks1[-2])/2),0))
    blue_cutoff2 = int(np.round(allpeaks2[-1]+((allpeaks2[-1] - allpeaks2[-2])/2)+2500,0))
    red_cutoff1 = int(np.round(allpeaks1[0]-((allpeaks1[1] - allpeaks1[0])/2),0))
    red_cutoff2 = int(np.round(allpeaks2[0]-((allpeaks2[1] - allpeaks2[0])/2)+2500,0))
    cond1_1 = (laser_ref_wl >= wl[ord][blue_cutoff1])
    cond1_2 = (laser_ref_wl >= wl[ord][blue_cutoff2])
    cond2_1 = (laser_ref_wl <= wl[ord][red_cutoff1])
    cond2_2 = (laser_ref_wl <= wl[ord][red_cutoff2])
    #these are the theoretical wavelengths from the NIST linelists
    lam1 = laser_ref_wl[np.logical_and(cond1_1,cond2_1)][::-1]
    lam2 = laser_ref_wl[np.logical_and(cond1_2,cond2_2)][::-1]
    lam1 = lam1[first_mask1][second_mask1][third_mask1]
    lam2 = lam2[first_mask2][second_mask2][third_mask2]
    
    x = np.r_[x1,x2]
    lam = np.r_[lam1,lam2]
    
    #check if the number of lines found equals the number of lines from the line list
    if verbose:
        print(len(x),len(lam))
    if len(x) != len(lam):
        print('fuganda')
        return 'fuganda'
    
    #fit polynomial to lambda as a function of x
    laser_fit = np.poly1d(np.polyfit(x, lam, deg_polynomial))
    
    if return_stats:
        stats[ord] = {}
        resid = laser_fit(x) - lam
        stats[ord]['resids'] = resid
        #mean error in RV for a single line = c * (stddev(resid) / mean(lambda))
        stats[ord]['single_rverr'] = 3e8 * (np.std(resid) / np.mean(lam))
        stats[ord]['rverr'] = 3e8 * (np.std(resid) / np.mean(lam)) / np.sqrt(len(lam))
        stats[ord]['n_lines'] = len(lam)
    
    #save to output dictionary
    laser_dispsol[ord] = laser_fit

    if timit:
        print('Time taken for finding Laser-comb wavelength solution: '+str(time.time() - start_time)+' seconds...')

    if return_stats:
        return laser_dispsol, stats 
    else:
        return laser_dispsol
        




# laser_dispsol2,stats2 = get_dispsol_from_laser(laserdata, laser_ref_wl, verbose=True, timit=True, return_stats=True, deg_polynomial=2)
# laser_dispsol3,stats3 = get_dispsol_from_laser(laserdata, laser_ref_wl, verbose=True, timit=True, return_stats=True, deg_polynomial=3)
# laser_dispsol5,stats5 = get_dispsol_from_laser(laserdata, laser_ref_wl, verbose=True, timit=True, return_stats=True, deg_polynomial=5)
# laser_dispsol11,stats11 = get_dispsol_from_laser(laserdata, laser_ref_wl, verbose=True, timit=True, return_stats=True, deg_polynomial=11)


###########################################################################################################




def fit_dispsol_2D(x_norm, ord_norm, WL, weights=None, polytype = 'chebyshev', poly_deg=5, debug_level=0):
    """
    Calculate 2D polynomial wavelength fit to normalized x and order values.

    x_norm: x-values (pixels) of all the lines, re-normalized to [-1,+1]
    m_norm: order numbers of all the lines, re-normalized to [-1,+1]
    orders: order numbers of all the lines

    polytype: either 'polynomial' (default), 'legendre', or 'chebyshev' are accepted
    """
    
    if polytype in ['Polynomial','polynomial','p','P']:
        p_init = models.Polynomial2D(poly_deg)
        if debug_level > 0:
            print('OK, using standard polynomials...')
    elif polytype in ['Chebyshev','chebyshev','c','C']:
        p_init = models.Chebyshev2D(poly_deg,poly_deg)
        if debug_level > 0:
            print('OK, using Chebyshev polynomials...')
    elif polytype in ['Legendre','legendre','l','L']:
        p_init = models.Legendre2D(poly_deg,poly_deg)  
        if debug_level > 0:
            print('OK, using Legendre polynomials...')   
    else:
        print("ERROR: polytype not recognised ['(P)olynomial' / '(C)hebyshev' / '(L)egendre']")    
        
    fit_p = fitting.LevMarLSQFitter()  

    with warnings.catch_warnings():
        # Ignore model linearity warning from the fitter
        warnings.simplefilter('ignore')
        p = fit_p(p_init, x_norm, ord_norm, WL, weights=weights)


#     if debug_level > 0:
#         plt.figure()
#         index_include = np.array(weights, dtype=bool)
#         plt.scatter(x_norm[index_include], WL[index_include], c=order_norm[index_include])
#         plt.scatter(x_norm[np.logical_not(index_include)], WL[np.logical_not(index_include)], facecolors='none',
#                     edgecolors='r')
# 
#         for x, o, oo, wl in zip(x_norm[index_include], order_norm[index_include], orders[index_include],
#                                 WL[index_include]):
#             plt.arrow(x, wl, 0, (p(x, o) / oo - wl) * 1000., head_width=0.00005, head_length=0.0001, width=0.00005)
# 
#         xi = np.linspace(min(x_norm[index_include]), max(x_norm[index_include]), 101)
#         yi = np.linspace(min(order_norm[index_include]), max(order_norm[index_include]), 101)
#         zi = griddata((x_norm[index_include], order_norm[index_include]),
#                       ((WL[index_include] - p(x_norm[index_include], order_norm[index_include]) / orders[
#                           index_include]) / np.mean(WL[index_include])) * 3e8,
#                       (xi[None, :], yi[:, None]), method='linear')
#         fig, ax = plt.subplots()
#         ax.set_xlim((np.min(xi), np.max(xi)))
#         ax.set_ylim((np.min(yi), np.max(yi)))
#         ax.set_xlabel('Detector x normalized')
#         ax.set_ylabel('order normalized')
#         plt.title('Legendre Polynomial Degree: ' + str(poly_deg) + "\n" + "#pars: " + str(len(p.parameters)))
# 
#         im = ax.imshow(zi, interpolation='nearest', extent=[np.min(xi), np.max(xi), np.min(yi), np.max(yi)])
# 
#         divider = make_axes_locatable(ax)
#         cax = divider.append_axes("right", size="5%", pad=0.05)
# 
#         cb = plt.colorbar(im, cax=cax)
#         cb.set_label('RV deviation [m/s]')
# 
#         plt.tight_layout()

    return p






# dispsol = np.load('/Users/christoph/UNSW/dispsol/lab_tests/thar_dispsol.npy').item()
# fitshapes = np.load('/Users/christoph/UNSW/dispsol/lab_tests/fitshapes.npy').item() 
# wavelengths = np.load('/Users/christoph/UNSW/dispsol/lab_tests/wavelengths.npy').item()
# wl_ref = np.load('/Users/christoph/UNSW/linelists/AAT_folder/wl_ref.npy').item()
# x = np.array([])
# m = np.array([])
# wl = np.array([])
# wl_ref_arr = np.array([])
# for ord in sorted(dispsol.keys()):
#     ordnum = int(ord[-2:])
#     x = np.append(x,fitshapes[ord]['x'])
#     order = np.append(order, np.repeat(ordnum,len(fitshapes[ord]['x'])))
#     wl = np.append(wl, dispsol[ord](fitshapes[ord]['x']))
#     wl_ref_arr = np.append(wl_ref_arr, wl_ref[ord])





def get_wavelength_solution(thflux, thflux2, poly_deg=5, laser=False, polytype='chebyshev', savetable=False, return_full=True, saveplots=False, timit=False, debug_level=0):
    """ 
    INPUT:
    'thflux'           : extracted 1-dim thorium / laser-only image 
    'poly_deg'         : the order of the polynomials to use in the fit (for both dimensions)
    'laser'            : boolean that tells the code whether it is a laser-comb spectrum or an arc-lamp spectrum
    'polytype'         : either 'polynomial', 'legendre', or 'chebyshev' (default) are accepted 
    'return_full'      : boolean - if TRUE, then the wavelength solution for each pixel for each order is returned; otherwise just the set of coefficients that describe it
    'saveplots'        : boolean - do you want to create plots for each order? 
    'savetable'        : boolean - if TRUE, then an output file is created, containing a summary of all lines used in the fit, and details about the fit
    'timit'            : time it...
    'debug_level'      : for debugging only
    
    OUTPUT:
    EITHER
    'p'      : functional form of the coefficients that describe the wavelength solution
    OR 
    'p_wl'   : wavelength solution for each pixel for each order (n_ord x n_pix numpy-array) 
    (selection between outputs is controlled by the 'return_full' keyword)
    
    TODO:
    clean-up the 2-version thing about which extracted spectrum to use (thflux / thflux2)
    include order 40 (m=65) as well (just 2 lines!?!?!?)
    figure out how to properly use weights here
    """
    
    if timit:
        start_time = time.time()
    
    #read in pre-defined thresholds (needed for line identification)
    thresholds = np.load('/Users/christoph/UNSW/linelists/AAT_folder/thresholds.npy').item()
    
    #wavelength solution from Zemax as a reference
    if saveplots:
        zemax_dispsol = np.load('/Users/christoph/UNSW/dispsol/mean_dispsol_by_orders_from_zemax.npy').item()   
    
    #prepare arrays for fitting
    x = np.array([])
    order = np.array([])
    m_order = np.array([])
    wl = np.array([])

    for ord in sorted(thflux.keys())[:-1]:    #don't have enough lines in order 40
        ordnum = ord[-2:]
        m = 105 - int(ordnum)
        print('OK, fitting '+ord+'   (m = '+str(m)+')')
        coll = thresholds['collapsed'][ord]
        if coll:
            data = thflux2[ord]
        else:
            data = thflux[ord]
        
        xx = np.arange(len(data))
        
#         if return_all_pars:
#             fitted_line_pos,fitted_line_sigma,fitted_line_amp = fit_emission_lines(data,return_all_pars=return_all_pars,varbeta=False,timit=False,verbose=False,thresh=thresholds['thresh'][ord],bgthresh=thresholds['bgthresh'][ord],maxthresh=thresholds['maxthresh'][ord])
#         else:
#             fitted_line_pos = fit_emission_lines(data,return_all_pars=return_all_pars,varbeta=False,timit=False,verbose=False,thresh=thresholds['thresh'][ord],bgthresh=thresholds['bgthresh'][ord],maxthresh=thresholds['maxthresh'][ord])
        fitted_line_pos = fit_emission_lines(data,return_all_pars=return_all_pars,varbeta=False,timit=False,verbose=False,thresh=thresholds['thresh'][ord],bgthresh=thresholds['bgthresh'][ord],maxthresh=thresholds['maxthresh'][ord])
        goodpeaks,mostpeaks,allpeaks = find_suitable_peaks(data,thresh=thresholds['thresh'][ord],bgthresh=thresholds['bgthresh'][ord],maxthresh=thresholds['maxthresh'][ord])    
        
        line_number, refwlord = readcol('/Users/christoph/UNSW/linelists/AAT_folder/ThAr_linelist_order_'+ordnum+'.dat',fsep=';',twod=False)
        #lam = refwlord.copy()  
        #wl_ref[ord] = lam
        
        mask_order = np.load('/Users/christoph/UNSW/linelists/posmasks/mask_order'+ordnum+'.npy')
        xord = fitted_line_pos[mask_order]
        #stupid python!?!?!?
        if ordnum == '30':
            xord = np.array([xord[0][0],xord[1][0],xord[2],xord[3]])
        
        if saveplots:
            zemax_wl = 10. * zemax_dispsol['order'+str(m)]['model'](xx[::-1])
        
        #fill arrays for fitting
        x = np.append(x, xord)
        order = np.append(order, np.repeat(int(ordnum), len(xord)))
        m_order = np.append(m_order, np.repeat(105 - int(ordnum), len(xord)))
        wl = np.append(wl, refwlord)
        
        
#         #perform the fit
#         fitdegpol = degpol
#         while fitdegpol > len(x)/2:
#             fitdegpol -= 1
#         if fitdegpol < 2:
#             fitdegpol = 2
#         thar_fit = np.poly1d(np.polyfit(x, lam, fitdegpol))
#         dispsol[ord] = thar_fit
#         if return_all_pars:
#             fitshapes[ord] = {}
#             fitshapes[ord]['x'] = x
#             fitshapes[ord]['y'] = P_id[ord](x)
#             fitshapes[ord]['FWHM'] = 2.*np.sqrt(2.*np.log(2.)) * fitted_line_sigma[mask_order]
#         
#         #calculate RMS of residuals in terms of RV
#         resid = thar_fit(x) - lam
#         rv_resid = 3e8 * resid / lam
#         rms = np.std(rv_resid)
#         
#         if saveplots:
#             #first figure: lambda vs x with fit and zemax dispsol
#             fig1 = plt.figure()
#             plt.plot(x,lam,'bo')
#             plt.plot(xx,thar_fit(xx),'g',label='fitted')
#             plt.plot(xx,zemax_wl,'r--',label='Zemax')
#             plt.title('Order '+str(m))
#             plt.xlabel('pixel number')
#             plt.ylabel(ur'wavelength [\u00c5]')
#             plt.text(3000,thar_fit(500),'n_lines = '+str(len(x)))
#             plt.text(3000,thar_fit(350),'deg_pol = '+str(fitdegpol))
#             plt.text(3000,thar_fit(100),'RMS = '+str(round(rms, 1))+' m/s')
#             plt.legend()
#             plt.savefig('/Users/christoph/UNSW/dispsol/lab_tests/fit_to_order_'+ordnum+'.pdf')
#             plt.close(fig1)
#             
#             #second figure: spectrum vs fitted dispsol
#             fig2 = plt.figure()
#             plt.plot(thar_fit(xx),data)
#             #plt.scatter(thar_fit(x), data[x.astype(int)], marker='o', color='r', s=40)
#             plt.scatter(thar_fit(goodpeaks), data[goodpeaks], marker='o', color='r', s=30)
#             plt.title('Order '+str(m))
#             plt.xlabel(ur'wavelength [\u00c5]')
#             plt.ylabel('counts')
#             plt.savefig('/Users/christoph/UNSW/dispsol/lab_tests/ThAr_order_'+ordnum+'.pdf')
#             plt.close(fig2)
    
    
    #re-normalize arrays to [-1,+1]
    x_norm = (x / ((len(data)-1)/2.)) - 1.
    order_norm = ((order-1) / (38./2.)) - 1.       #TEMP, TODO, FUGANDA, PLEASE FIX ME!!!!!
    #order_norm = ((m-1) / ((len(P_id)-1)/2.)) - 1.
           
    #call the fitting routine
    p = fit_dispsol_2D(x_norm, order_norm, wl, weights=None, polytype = polytype, poly_deg=poly_deg, debug_level=0)         
            

#     if return_all_pars:
#         return dispsol,fitshapes
#     else:
#         return dispsol

    if savetable:
        now = datetime.datetime.now()
        model_wl = p(x_norm, order_norm)
        resid = wl - model_wl
        outfn = '/Users/christoph/UNSW/linelists/AAT_folder/lines_used_in_fit_as_of_'+str(now)[:10]+'.dat'
        outfn = open(outfn, 'w')
        outfn.write('line number   order_number   physical_order_number     pixel      reference_wl[A]   model_wl[A]    residuals[A]\n')
        outfn.write('=====================================================================================================================\n')
        for i in range(len(x)):
                outfn.write("   %3d             %2d                 %3d           %11.6f     %11.6f     %11.6f     %9.6f\n" %(i+1, order[i], m_order[i], x[i], wl[i], model_wl[i], resid[i]))
        outfn.close()
              

    if return_full:
        #xx = np.arange(4112)            [already done above]
        xxn = (xx / ((len(data)-1)/2.)) - 1.
        oo = np.arange(1,len(thflux))
        oon = ((oo-1) / (38./2.)) - 1.        #TEMP, TODO, FUGANDA, PLEASE FIX ME!!!!!
        #oon = ((oo-1) / ((len(thflux)-1)/2.)) - 1.   
        X,O = np.meshgrid(xxn,oon)
        p_wl = p(X,O)

    
    if timit:
        print('Time elapsed: ',time.time() - start_time,' seconds')
        

    if return_full:
        return p,p_wl
    else:
        return p





def get_simu_dispsol():
    #read dispersion solution from file
    dispsol = np.load('/Users/christoph/UNSW/dispsol/mean_dispsol_by_orders_from_zemax.npy').item()
    
    #read extracted spectrum from files (obviously this needs to be improved)
    xx = np.arange(4096)
    
    #this is so as to match the order number with the physical order number (66 <= m <= 108)
    # order01 corresponds to m=66
    # order43 corresponds to m=108
    wl = {}
    for ord in dispsol.keys():
        m = ord[5:]
        ordnum = str(int(m)-65).zfill(2)
        wl['order_'+ordnum] = dispsol['order'+m]['model'](xx)

    return wl







