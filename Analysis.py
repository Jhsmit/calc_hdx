#!/usr/bin/env python

# Analysis/plotting functions for HDX analysis

import Functions
import numpy as np
import matplotlib.pyplot as plt
import os, glob, copy, itertools
from scipy.stats import pearsonr as correl
from matplotlib.backends.backend_pdf import PdfPages
from cycler import cycler

### Define defaults for matplotlib plots
plt.rc('lines', linewidth=2)
plt.rc('axes', prop_cycle=(cycler('color', ['k','b','r','g','c','m','y','orange']))) # Color cycle starts with black
#plt.rc('text', usetex=True)

### Classes
class Analyze():
    """Class to contain results and analysis methods for HDX predictions"""

    def __init__(self, resobj, top, **extra_params):
        """Initialises Analyze object from a Method object with by-residue results"""
        try:
            self.reslist = resobj.reslist
            # Cumulative resfracs = 3D-array[chunk, resfrac, time]
            self.resfracs = np.reshape(resobj.resfracs, (1, len(resobj.resfracs), len(resobj.resfracs[0])))
            self.c_resfracs = np.copy(self.resfracs)
            # Cumulative PFs = 2D-array[chunk, PFs]
            self.pfs = np.reshape(resobj.pfs, (1, len(resobj.pfs)))
            self.c_pfs = np.copy(self.pfs)
            # Cumulative n_frames = 1D-array[n_frames]
            self.n_frames = np.atleast_1d(resobj.t.n_frames)
            self.c_n_frames = np.copy(self.n_frames)
            # Topology & rates
            self.rates = resobj.rates
            self.top = top
        except AttributeError:
            raise Functions.HDX_Error("Error when copying results from prediction to analysis objects - have you made any HDX predictions yet?")
        self.params = resobj.params
        try:
            self.params.update(extra_params)
        except (TypeError, ValueError):
            print("Couldn't load extra parameters for analysis (maybe they weren't provided?).\nUsing previous parameters from %s object." % resobj)
                   
    def __add__(self, other):
        """Add resfracs, pfs and n_frames from a second results object and
           update cumulative sums.

           Usage: __add__(self, other)"""

        if isinstance(other, Analyze):
#            try:
                if not all((np.array_equal(self.reslist, other.reslist), np.array_equal(self.rates, other.rates))):
                    print("Reslist or rates of added Analyze objects differ. Not adding them!")
                    return self
                new = copy.deepcopy(self)
                # Append n_frames
                new.n_frames = np.append(new.n_frames, other.n_frames)
                new.c_n_frames = np.cumsum(new.n_frames)
                
                # Calc running ave of PFs = 2D-array[chunk, PFs]
                new.pfs = np.concatenate((new.pfs, other.pfs), axis=0)
                _ = np.copy(new.pfs)
                for frames, curr_pf in zip(new.n_frames, _):
                    curr_pf *= frames
                new.c_pfs = np.cumsum(_, axis=0)
                for tot_frames, tot_pf in zip(new.c_n_frames, new.c_pfs):
                    tot_pf /= tot_frames
            
                # Calc running ave of resfracs = 3D-array[chunk, resfrac, time]
                new.resfracs = np.concatenate((new.resfracs, other.resfracs), axis=0)
                _ = np.zeros(new.resfracs[0].shape)
                # Redo resfrac calculation based on running average of pfs
                # N.B. Due to the exponential this is NOT just an average of the resfrac blocks
                for i2, t in enumerate(new.params['times']):
                    def _residue_fraction(pf, k, time=t):
                        return 1 - np.exp(-k / pf * time)
                    for i1, curr_frac in enumerate(itertools.imap(_residue_fraction, new.c_pfs[-1], new.rates)):
                        _[i1,i2] = curr_frac
                new.c_resfracs = np.concatenate((new.c_resfracs, \
                                                 np.reshape(_, (1, len(new.reslist), len(new.params['times'])))), \
                                                 axis=0)

                return new
 
#            except AttributeError:
#                raise Functions.HDX_Error("Error when adding analysis objects - have you made any HDX predictions yet?")
        else:
            return self

    def read_segfile(self):

        # segfile should contain at most 2 columns, startres & endres
        try:
            self.segres = np.loadtxt(self.params['segfile'], dtype='i8')  # ResIDs will be converted to indices with dictionary in segments function
            for v1, v2 in self.segres:
                pass
        except ValueError:
            raise Functions.HDX_Error("There's a problem reading the values in your segments file: %s \n"
                                      "File should contain 2 columns of integers, separated by spaces.")

    def read_expfile(self):
        """Reads an experimental data file for comparison to predicted data.

           Experimental results file should be formatted as follows:
           Seg_start   Seg_end   Time_1   Time_2   Time_3 ... [Time_n]

           This is the same format as the printout of predicted results"""

        # Check I'm not loading in too many timepoints
        try:
            expt = np.loadtxt(self.params['expfile'], dtype=[ ('segres', np.int32, (2,)),\
                              ('fracs', np.float64, (len(self.params['times']),)) ])
        except ValueError:
            raise Functions.HDX_Error("""There's a problem with your experimental data file, it's shorter than the number of timepoints you evaluated""")
        # Now check I'm not loading in too few timepoints
        try:
            expt = np.loadtxt(self.params['expfile'], dtype=[ ('segres', np.int32, (2,)),\
                              ('fracs', np.float64, (len(self.params['times']) + 1,)) ])
            raise Functions.HDX_Error("""There's a problem with your experimental data file, it's longer than the number of timepoints you evaluated""")
        except ValueError:
            pass
        # Check expt = predicted
        if np.array_equal(self.segres, expt['segres']):
            self.expfracs = expt['fracs']
        else:
            raise Functions.HDX_Error("The experimental segments read from %s and predicted segments read from %s don't match!" % (self.params['segfile'], self.params['expfile']))
                                       
    
    def segments(self, top):
        """Function to average residue deuterated fractions over
           a given set of peptide segments. The first residue in each
           segment will not be included in the averaging, as it is assumed
           to be 100% back-exchanged during analysis.
    
           Residue indices provided in the given list are converted to 
           residue IDs from the given trajectory's topology. Currently this
           remumbering will only work for single chain topologies with sequential
           numbering. If a residue in a segment is not found (e.g. a truncated
           N/C terminus), the next residue is chosen as the start/end point instead.
 
           Writes info on skipped residues to logfile "HDX_analysis.log" by default
           and the segment/average deuteration information to "Segment_average_fractions.dat"

           Usage: segments(traj, reslist, fracs, segfile_name, times, [ log="HDX_analysis.log" ])
           Returns: [n_segs, 2] 2D numpy array of segment start/end residue IDs, 
                [n_segs, n_times] 2D numpy array of segment deuterated fractions at each timepoint"""

        res2idx = {}
        with open(self.params['logfile'], 'a') as f:
            f.write("Now converting residue numbers to indices for segment averaging:\n")
        for idx, res in enumerate(top.residues):
            if res.is_protein:
#                res2idx[str(res.chain.index) + '-' + str(res.resSeq)] = idx
                res2idx[res.resSeq] = idx # Only works for single chain or sequential numbers, no re-use of resnums
            else:
                with open(self.params['logfile'], 'a') as f:
                    f.write("Skipping residue: %s, not a protein residue\n" % res)
        
        
        self.read_segfile()
        try:
            aves = np.zeros((len(self.resfracs), len(self.segres), len(self.params['times'])))
            c_aves = np.zeros((len(self.c_resfracs), len(self.segres), len(self.params['times'])))
        except TypeError:
            aves = np.zeros((len(self.resfracs), len(self.segres), 1))
            c_aves = np.zeros((len(self.c_resfracs), len(self.segres), 1))
            self.params['times'] = [self.params['times']]    

        # Info for 'skip_first'
        if self.params['skip_first']:
            for i1, s in enumerate(self.segres):
                with open(self.params['logfile'], 'a') as f:
                    f.write("'Skip_first' is set. Not including residue %s in averaging for segment %s-%s.\n" \
                            % (top.residue(res2idx[s[0]]), s[0], s[1]))
        else:
            for i1, s in enumerate(self.segres):
                with open(self.params['logfile'], 'a') as f:
                    f.write("'Skip_first' is NOT set. Including residue %s in averaging for segment %s-%s.\n" \
                            % (top.residue(res2idx[s[0]]), s[0], s[1]))

        # Calc average fractions for each chunk                    
        for i0, chunk in enumerate(self.resfracs):
            for i2, t in enumerate(self.params['times']):
                for i1, s in enumerate(self.segres):
                    try:
                        start = res2idx[s[0]]
                    except KeyError:
                        with open(self.params['logfile'], 'a') as f:
                            f.write("Didn't find residue %s in protein. Using residue %s as startpoint instead.\n" \
                                 % (s[0], top.residue(0)))
                        start = 0
                    try:
                        end = res2idx[s[1]]
                    except KeyError:
                        with open(self.params['logfile'], 'a') as f:
                            f.write("Didn't find residue %s in protein. Using residue %s as endpoint instead.\n" \
                                     % (s[0], top.residue(-1)))
                        end = top.residue(-1).index
    
                    if self.params['skip_first']:
                        idxs = np.where(np.logical_and( self.reslist > start, self.reslist <= end ))[0] # > start skips
                    else:
                        idxs = np.where(np.logical_and( self.reslist >= start, self.reslist <= end ))[0] # >= start incs

                    aves[i0, i1, i2] = np.mean(chunk[idxs, i2])

        # Do the same for cumulative resfracs
        for i0, cchunk in enumerate(self.c_resfracs):
            for i2, t in enumerate(self.params['times']):
                for i1, s in enumerate(self.segres):
                    try:
                        start = res2idx[s[0]]
                    except KeyError:
                        with open(self.params['logfile'], 'a') as f:
                            f.write("Cumulative segment averages: "
                                    "Didn't find residue %s in protein. Using residue %s as startpoint instead.\n" \
                                    % (s[0], top.residue(0)))
                        start = 0
                    try:
                        end = res2idx[s[1]]
                    except KeyError:
                        with open(self.params['logfile'], 'a') as f:
                            f.write("Cumulative segment averages: "
                                    "Didn't find residue %s in protein. Using residue %s as endpoint instead.\n" \
                                    % (s[0], top.residue(-1)))
                        end = top.residue(-1).index
    
                    if self.params['skip_first']:
                        idxs = np.where(np.logical_and( self.reslist > start, self.reslist <= end ))[0] # > start skips
                    else:
                        idxs = np.where(np.logical_and( self.reslist >= start, self.reslist <= end ))[0] # >= start incs

                    c_aves[i0, i1, i2] = np.mean(cchunk[idxs, i2])
                    
        # Write average fractions file for each chunk
        # N.B Again, these will NOT add up to the c_segfracs value, which is recalc'd using
        # the exponential decay and the mean PF at a given timepoint (not just a straight ave
        # of the block averaged resfracs)
        for chunkave in aves:
            if os.path.exists(self.params['outprefix']+"Segment_average_fractions.dat"):
                filenum = len(glob.glob(self.params['outprefix']+"Segment_average_fractions*"))
                np.savetxt(self.params['outprefix']+"Segment_average_fractions_chunk_%d.dat" % (filenum+1), \
                           np.hstack((self.segres, chunkave)), \
                           fmt='%6d %6d ' + '%8.5f '*len(self.params['times']), header="Res 1   Res2  Times / min: %s" \
                           % ' '.join([ str(t) for t in self.params['times'] ]))
            else:
                np.savetxt(self.params['outprefix']+"Segment_average_fractions.dat", np.hstack((self.segres, chunkave)), \
                           fmt='%6d %6d ' + '%8.5f '*len(self.params['times']), header="Res 1   Res2  Times / min: %s" \
                           % ' '.join([ str(t) for t in self.params['times'] ]))

        with open(self.params['logfile'], 'a') as f:
            f.write("Segment averaging complete.\n")

        return aves, c_aves


    def desc_stats(self):
        """Calculates descriptive statistics of segments compared to expt
           for all timepoints"""

        self.correls = np.zeros(len(self.params['times']))
        self.MUE = np.zeros(len(self.params['times']))
        self.MSE = np.zeros(len(self.params['times']))
        for idx, t in enumerate(self.params['times']):
            self.correls[idx] = correl(self.c_segfracs[-1][:,idx], self.expfracs[:,idx])[0]
            self.MSE[idx] = np.mean(self.c_segfracs[-1][:,idx] - self.expfracs[:,idx])
            self.MUE[idx] = np.mean(np.abs(self.c_segfracs[-1][:,idx] - self.expfracs[:,idx]))
        with open(self.params['outprefix']+"Descriptive_statistics.dat", 'a') as f:
            np.savetxt(f, self.correls, header="Pearson's R correlation, Times / min: %s" \
                       % ' '.join([ str(t) for t in self.params['times'] ]), fmt='%8.6f')
            np.savetxt(f, self.MSE, header="Mean signed error / frac, Pred. - Expt., Times / min: %s" \
                       % ' '.join([ str(t) for t in self.params['times'] ]), fmt='%10.8f')
            np.savetxt(f, self.MUE, header="Mean unsigned error / frac, Pred. - Expt., Times / min: %s" \
                       % ' '.join([ str(t) for t in self.params['times'] ]), fmt='%10.8f')

    def print_summaries(self):
        """Print summary PF, resfrac and segment results - for example of a method
           object that has had results summed over multiple chunks."""

        
        # Save PFs to 'SUMMARY' file
        try:
            rids = np.asarray([ self.top.residue(i).resSeq for i in self.reslist ])
            if os.path.exists(self.params['outprefix']+"SUMMARY_protection_factors.dat"):
                filenum = len(glob.glob(self.params['outprefix']+"SUMMARY_protection_factors*"))
                np.savetxt(self.params['outprefix']+"SUMMARY_protection_factors_%d.dat" % (filenum+1), \
                           np.stack((rids, self.c_pfs[-1]), axis=1), fmt=['%7d','%18.8f'], \
                           header="ResID  Protection factor") # Use residue indices internally, print out IDs
            else:    
                np.savetxt(self.params['outprefix']+"SUMMARY_protection_factors.dat", np.stack((rids, self.c_pfs[-1]), axis=1), \
                           fmt=['%7d','%18.8f'], header="ResID  Protection factor") # Use residue indices internally, print out IDs
        except AttributeError:
            raise Functions.HDX_Error("Can't write summary protection factors - perhaps you haven't calculated them yet?")

        # Save residue deuterated fractions to 'SUMMARY' file
        try:
            if os.path.exists(self.params['outprefix']+"SUMMARY_residue_fractions.dat"):
                filenum = len(glob.glob(self.params['outprefix']+"SUMMARY_residue_fractions*"))
                np.savetxt(self.params['outprefix']+"SUMMARY_residue_fractions_%d.dat" % (filenum+1), \
                           np.concatenate((np.reshape(rids, (len(self.reslist),1)), self.c_resfracs[-1]), axis=1), \
                           fmt='%7d ' + '%8.5f '*len(self.params['times']), \
                           header="ResID  Deuterated fraction, Times / min: %s" \
                           % ' '.join([ str(t) for t in self.params['times'] ])) # Use residue indices internally, print out IDs
            else:    
                np.savetxt(self.params['outprefix']+"SUMMARY_residue_fractions.dat", \
                           np.concatenate((np.reshape(rids, (len(self.reslist),1)), self.c_resfracs[-1]), axis=1), \
                           fmt='%7d ' + '%8.5f '*len(self.params['times']), \
                           header="ResID  Deuterated fraction, Times / min: %s" \
                           % ' '.join([ str(t) for t in self.params['times'] ])) # Use residue indices internally, print out IDs
        except AttributeError:
            raise Functions.HDX_Error("Can't write summary residue fractions - perhaps you haven't calculated them yet?")


        # Save segment deuterated averages to 'SUMMARY' file
        try:
            if os.path.exists(self.params['outprefix']+"SUMMARY_segment_average_fractions.dat"):
                filenum = len(glob.glob(self.params['outprefix']+"SUMMARY_segment_average_fractions*"))
                np.savetxt(self.params['outprefix']+"SUMMARY_segment_average_fractions_%d.dat" % (filenum+1), \
                           np.hstack((self.segres, self.c_segfracs[-1])), \
                           fmt='%6d %6d ' + '%8.5f '*len(self.params['times']), header="Res 1   Res2  Times / min: %s" \
                           % ' '.join([ str(t) for t in self.params['times'] ]))
            else:
                np.savetxt(self.params['outprefix']+"SUMMARY_segment_average_fractions.dat", \
                           np.hstack((self.segres, self.c_segfracs[-1])), \
                           fmt='%6d %6d ' + '%8.5f '*len(self.params['times']), header="Res 1   Res2  Times / min: %s" \
                           % ' '.join([ str(t) for t in self.params['times'] ]))

        except AttributeError:
            raise Functions.HDX_Error("Can't write summary segment fractions - perhaps you haven't calculated them yet?")
        
    
    def run(self, figs=False):
        """Runs a by-segment HDX prediction and optionally graphs results"""

        self.read_segfile()
        self.segfracs, self.c_segfracs = self.segments(self.top)
        if self.params['expfile'] is not None:
            self.read_expfile()
            self.desc_stats()

### Plotting Class
class Plots():
    """Class to plot results of HDX predictions"""

    def __init__(self, aobj):
        """Initialise a Plots object from an Analyze object"""
        if isinstance(aobj, Analyze):
            self.results = aobj
        else:
            raise Functions.HDX_Error("Can't initialize a Plots object from anything"\
                                      "other than a completed Analyze object.")

    def choose_plots(self, **override_opts):
        """Choose available plots based on results in Analyze object.
           Normally these will be automatically chosen based on available
           data, but switch can be overriden by providing kwargs.

           Available plots:
           df_curve : By-segment deuterated fractions for all timepoints
           df_convergence : Convergence of by-segment deuterated fractions across all simulation chunks
           seg_curve : By-segment predictions across all timepoints
           seg_convergence : Convergence of by-segment predictions across all timepoints
           pf_curve : By-residue protection factors
           tot_pf   : Convergence of total protection factor across all simulation chunks
           _expt_overlay : Option to switch on/off overlay of experimental values on all relevant plots
           _block_ave : Option to switch on/off block averaging plots as well as convergence (running ave)

           Sets Plots.avail attribute with dictionary of results."""

        self.avail = { 'df_curve' : False,
                       'df_convergence' : False,
                       'seg_curve' : False,
                       'seg_range' : False,
                       'pf_byres' : False,
                       'tot_pf' : False,
                       '_expt_overlay' : False,
                       '_block_ave' : False } # Currently unused

        try:
            self.results.c_resfracs[-1]
            self.avail['df_curve'] = True
            if len(self.results.c_resfracs) > 1:
                self.avail['df_convergence'] = True
        except (AttributeError, IndexError):
            pass
        try:
            self.results.c_segfracs[-1]
            self.avail['seg_curve'] = True
            if len(self.results.c_segfracs) > 1:
                self.avail['seg_convergence'] = True
        except (AttributeError, IndexError):
            pass
        try:
            self.avail['pf_curve'] = ( len(self.results.c_pfs[-1]) == len(self.results.reslist) )
            if len(self.results.c_pfs) > 1:
                self.avail['tot_pf'] = True
        except (AttributeError, IndexError):
            pass
        try:
            if len(self.results.c_pfs) > 1:
                self.avail['tot_pf'] = True
        except AttributeError:
            pass
        try:
            # Other data soundness checks (for times, segres) are in Analyze.read_expfile
            self.avail['_expt_overlay'] = ( len(self.results.c_segfracs[-1]) == len(self.results.expfracs) )
        except (AttributeError, IndexError):
            pass

        # Overrides
        try:
            self.avail.update(override_opts)
            print("Available plots manually overriden for plots: %s" % ", ".join(override_opts.keys()))
        except (TypeError, ValueError):
            print("Available plots automatically chosen without overrides")
        
    def df_curve(self):
        """Plot a predicted deuteration curve for each segment. Plots are optionally
           overlaid with experimental curves, according to the value of
           Plots.avail['_expt_overlay'].

           Plots are saved to a multi-page PDF file df_curves.pdf, with up to
           8 segments per page.""" 

        def _plot_df_curve(ax, segdata, overlay_ys=None, **plot_opts):
            xs, ys = self.results.params['times'], segdata[2:]
            ax.plot(xs, ys, marker='x', color='black', linewidth=2, linestyle='-', label="Predicted", **plot_opts)
            ax.set_title("Segment %d-%d" % (segdata[0], segdata[1]), fontsize=9)
            ax.set_xlim(0.0, xs[-1])
            ax.set_ylim(0.0, 1.0)
            ax.set_xbound(upper=xs[-1])

            if overlay_ys is not None:
                ax.plot(xs, overlay_ys, marker='+', color='blue', linewidth=2, linestyle='--', label="Experimental")
            ax.legend(fontsize=6)

        def _plot_pdf_page_df(startslice, endslice):
###         subplot2grid implementation?
#            fig = plt.Figure(figsize=(8.5, 11)) # Letter
#            axis_idxs = []
#            for row in range(4):
#                for col in range(2):
#                    axis_idxs.append((row, col))
#            data_and_axes = zip(data_slice, axis_idxs):
#            d1 = data_and_axes.pop(0)
#            _plot_df_curve(
#            d2 = data_and_axes.pop(0)
#            ax1 = plt.subplot2grid((4,2),d1[1])
#            _plot_df_curve(ax1, d1[0]
#            ax7 = plt.subplot2grid((4,2),d7[1], sharey=ax8)
#            for odd in da

            fig, axs = plt.subplots(ncols=2, nrows=4, sharex=True, \
                                    sharey=True, figsize=(8.5, 11)) # Letter
            fig.suptitle("Deuterated fractions against time", fontsize=14)
            for ax in axs[:,0]:
                ax.set_ylabel("Deuterated fraction")
            for ax in axs[-1,:]:
                ax.set_xlabel("Time / min")
            axs = axs.flatten()
            if self.avail['_expt_overlay']:
                for a, predsegs, expt in zip(axs, \
                                             np.hstack((self.results.segres, self.results.c_segfracs[-1]))[startslice:endslice+1], \
                                             self.results.expfracs[startslice:endslice+1]):
                    _plot_df_curve(a, predsegs, overlay_ys=expt)
            else:
                for a, predsegs in zip(axs, np.hstack((self.results.segres, self.results.c_segfracs[-1]))[startslice:endslice+1]):
                    _plot_df_curve(a, predsegs)

            return fig

        with PdfPages("df_curves.pdf") as pdf:
            pages = int(len(self.results.c_segfracs[-1]) / 8) + 1 # Ceiling
            try:
                for pg in range(1, pages+1):
                    currfig = _plot_pdf_page_df(8*(pg-1), 8*pg)
                    pdf.savefig(currfig)
                    plt.close()
            except IndexError:
                currfig = _plot_pdf_page_df(8*(pg-1), len(self.results.c_segfracs[-1])) 
                pdf.savefig(currfig)
                plt.close()


    def df_convergence(self):
        """Plot the running average of predicted deuteration fractions for each segment.
           
           Plots are saved to a multi-page PDF file df_convergence.pdf, with one
           segment per page.""" 

        def _plot_df_convergence(block_fracs, cumul_fracs, seg):
            fig = plt.figure(figsize=(11, 8.5)) # Letter
            fig.suptitle("Convergence of predicted fractions across trajectory", fontsize=16)
            ax = fig.gca()
            ax.set_title("Segment %s-%s" % (seg[0], seg[1]), fontsize=12)
            xs = self.results.c_n_frames
            for timeidx in range(len(self.results.params['times'])):
                ax.plot(xs, cumul_fracs[:, timeidx], label="Time %s min" % self.results.params['times'][timeidx])
                ax.scatter(xs, block_fracs[:, timeidx], marker='x')
            ax.set_ylim(0.0, 1.25) # Space for legend
            ax.set_yticks(np.arange(0.0,1.2,0.2))
            ax.set_xlim(0, xs[-1] * 1.05) 
            ax.legend(loc='upper center', fontsize=8)
            return fig
                
        with PdfPages("df_convergence.pdf") as pdf:
            for i, currseg in enumerate(self.results.segres):
                currfig = _plot_df_convergence(self.results.segfracs[:,i,:], \
                                               self.results.c_segfracs[:,i,:], \
                                               currseg)
                pdf.savefig(currfig)
                plt.close()

    def seg_curve(self):
        """Plot by-segment deuterated fractions at a given timepoint.
           
           Plots are saved to a multi-page PDF file seg_curves.pdf, with one
           timepoint per page and a final plot of all timepoints. Plots are
           optionally overlaid with experimental curves, according to the 
           value of Plots.avail['_expt_overlay'].""" 

        def _plot_seg_curve(cumul_fracs, seglist, time, overlay_fracs=None):
            fig = plt.figure(figsize=(11,8.5)) # Letter
            ax = fig.gca()
            xs = range(1,len(seglist)+1)
            labels = [ str(i[0])+"-"+str(i[1]) for i in seglist ]
            ax.set_title("Time = %s min" % time, fontsize=12)
            ax.set_xticks(xs)
            ax.set_xticklabels(labels, rotation='vertical')
            ax.set_ylabel("Deuterated fraction")
            ax.set_xlabel("Peptide segment")
            ax.set_ylim(0.0, 1.0)

            if overlay_fracs is not None:
                ax.plot(xs, cumul_fracs, \
                        label="Predicted fraction, R = %3.2f" % self.results.correls[timeidx])
                ax.plot(xs, overlay_fracs, \
                        label="Experimental fraction", linestyle='--')
                fig.suptitle("By-segment predicted & experimental deuterated fractions", \
                             fontsize=16)
            else:
                ax.plot(xs, cumul_fracs, label="Predicted fraction")
                fig.suptitle("By-segment predicted deuterated fractions", \
                             fontsize=16)
            ax.legend()
            return fig

        def _plot_all_seg_curves(cumul_fracs, seglist, times, overlay_fracs=None):
            fig = plt.figure(figsize=(11,8.5)) # Letter
            ax = fig.gca()
            xs = range(1,len(seglist)+1)
            labels = [ str(i[0])+"-"+str(i[1]) for i in seglist ]
            ax.set_title("All timepoints (times in min)", fontsize=12)
            ax.set_xticks(xs)
            ax.set_xticklabels(labels, rotation='vertical')
            ax.set_ylabel("Deuterated fraction")
            ax.set_xlabel("Peptide segment")
            ax.set_ylim(0.0, 1.0)

            for timeidx, t in enumerate(times):
                if overlay_fracs is not None:
                    predline = ax.plot(xs, cumul_fracs[:,timeidx], \
                                       label="Predicted @ time %s, R = %3.2f" \
                                       % (t, self.results.correls[timeidx]))
                    ax.plot(xs, overlay_fracs[:, timeidx], \
                            label="Experimental @ time %s" % t, linestyle='--', \
                            color=predline[0].get_color(), linewidth=1, alpha=0.5)
                    fig.suptitle("By-segment predicted & experimental deuterated fractions", \
                                 fontsize=16)
                else:
                    ax.plot(xs, cumul_fracs[:, timeidx], label="Predicted @ time %s" % t)
                    fig.suptitle("By-segment predicted deuterated fractions", \
                                 fontsize=16)
            ax.legend()
            return fig
                    
        with PdfPages("seg_curves.pdf") as pdf:
            # Single timepoint plots
            for timeidx, t in enumerate(self.results.params['times']):
                if self.avail['_expt_overlay']:
                    currfig = _plot_seg_curve(self.results.c_segfracs[-1,:,timeidx], \
                                              self.results.segres, t, self.results.expfracs[:,timeidx])
                else:
                    currfig = _plot_seg_curve(self.results.c_segfracs[-1,:,timeidx], \
                                              self.results.segres, t)
                pdf.savefig(currfig)
                plt.close()
            # All timepoint plot
            if self.avail['_expt_overlay']:
                currfig = _plot_all_seg_curves(self.results.c_segfracs[-1], self.results.segres, \
                                     self.results.params['times'], overlay_fracs=self.results.expfracs)
            else:
                currfig = _plot_all_seg_curves(self.results.c_segfracs[-1], self.results.segres, \
                                     self.results.params['times'])
            pdf.savefig(currfig)
            plt.close()


    def seg_range(self):
        """Plot by-segment deuterated fractions at a given timepoint, with
           shaded standard deviation of by-segment fractions from block averages.
           
           Plots are saved to a multi-page PDF file seg_range.pdf, with one
           timepoint per page.""" 

        def _plot_seg_curve(cumul_fracs, seglist, blocksize, time, ax):
            xs = range(1,len(seglist)+1)
            labels = [ str(i[0])+"-"+str(i[1]) for i in seglist ]
            ax.set_title("Time = %s min, block size = %d" % (time, blocksize), fontsize=12)
            ax.set_xticks(xs)
            ax.set_xticklabels(labels, rotation='vertical')
            ax.set_ylabel("Deuterated fraction")
            ax.set_xlabel("Peptide segment")
            ax.set_ylim(0.0, 1.0)

            ax.plot(xs, cumul_fracs, label="Final predicted fraction")
            return ax

        def _fill_seg_range(seg_fracs, seglist, ax, cumul_fracs=None):
            xs = range(1,len(seglist)+1)
            segmaxs, segmins = np.zeros(seg_fracs.shape[1]), np.zeros(seg_fracs.shape[1])
#            for segidx in range(len(segmaxs)):
#                segmaxs[segidx] = np.max(seg_fracs[:, segidx])
#                segmins[segidx] = np.min(seg_fracs[:, segidx])
            # Optional +/- std dev
            for segidx in range(len(segmaxs)):
                segmaxs[segidx] = np.std(seg_fracs[:, segidx])
                segmins[segidx] = -1 * np.std(seg_fracs[:, segidx])
            segmaxs += cumul_fracs
            segmins += cumul_fracs
            ax.fill_between(xs, segmaxs, segmins, color='gray', alpha=0.3, label="Std. Dev. across trajectory blocks")
            return ax

        with PdfPages("seg_range.pdf") as pdf:
            for timeidx, currtime in enumerate(self.results.params['times']):
                fig = plt.figure(figsize=(11, 8.5))
                ax1 = fig.gca()
                fig.suptitle("Variation in by-segment predicted deuterated fractions across trajectory", \
                             fontsize=16)
                ax1 = _plot_seg_curve(self.results.c_segfracs[-1,:,timeidx], self.results.segres, \
                                      self.results.n_frames[0], \
                                      currtime, ax1)
#                ax1 = _fill_seg_range(self.results.segfracs[:,:,timeidx], self.results.segres, ax1)
                 # Optional +/- std.dev
                ax1 = _fill_seg_range(self.results.segfracs[:,:,timeidx], self.results.segres, ax1, \
                                      self.results.c_segfracs[-1,:,timeidx])
                ax1.legend()
                pdf.savefig(fig)
                plt.close()
            
    def pf_byres(self):
        """Plot by-residue protection factors averaged over all frames,
           both in raw and log10 scaled forms.
           
           Plots are saved to a multi-page PDF file pf_byres.pdf""" 

        with PdfPages("pf_byres.pdf") as pdf:
            # Raw PFs
            fig = plt.figure(figsize=(11, 8.5)) # Letter
            ax = fig.gca()
            initial, final = self.results.reslist[0], self.results.reslist[-1]
            xs = self.results.reslist
            xticknums = [initial]
            intermedx = range(50, final - 24, 50) # Residue labels at 50 res intervals
                                                  # skipping last label if it's within 25 of the previous
            xticknums.extend(intermedx)
            xticknums.append(final)
            ax.plot(xs, self.results.c_pfs[-1], linewidth=1)
            ax.set_title("Mean by-residue protection factors", fontsize=16)
            ax.set_ylabel("Protection factor")
            ax.set_xlabel("Residue")
            ax.set_xticks(xticknums)
            pdf.savefig(fig)
            plt.close()

            # Log PFs
            fig = plt.figure(figsize=(11, 8.5)) # Letter
            ax = fig.gca()
            initial, final = self.results.reslist[0], self.results.reslist[-1]
            xs = self.results.reslist
            xticknums = [initial]
            intermedx = range(50, final - 24, 50) # Residue labels at 50 res intervals
                                                  # skipping last label if it's within 25 of the previous
            xticknums.extend(intermedx)
            xticknums.append(final)
            ax.plot(xs, np.log10(self.results.c_pfs[-1]), linewidth=1)
            ax.set_title("Log of mean by-residue protection factors", fontsize=16)
            ax.set_ylabel(r'$log_{10}$(Protection factor)')
            ax.set_xlabel("Residue")
            ax.set_xticks(xticknums)
            pdf.savefig(fig)
            plt.close()


### Add further classes below here
