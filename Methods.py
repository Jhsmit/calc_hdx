#!/usr/bin/env python

# Class for HDX trajectories, inherited from MDTraj
from __future__ import print_function
from __future__ import division
# 
import mdtraj as md
import numpy as np
import os, glob, itertools
import Functions, DfPred

class Radou(DfPred.DfPredictor):
    """Class for Radou-style analysis. Subclass of DfPredictor.
       Initialises with a dictionary of default parameters for analysis,
       accessible as Radou.params

       Default parameters can either be updated directly in the Radou.params
       dictionary or by supplying a extra parameters as kwargs during
       initialisation, e.g.: Radou(cut_nc=1.0) or Radou(**param_dict)

       Run a by-residue deuterated fraction prediction with these parameters
       using the Radou.run method."""

    def __init__(self, **extra_params):
        """Initialises parameters for Radou-style analysis.
           See self.params for default values"""
        # Initialise main parameters with defaults
        radouparams = { 'hbond_method' : 'contacts',
                        'cut_Nc' : 0.65,
                        'cut_Nh' : 0.24,
                        'bh_dist' : 0.25,
                        'bh_ang' : 120.0,
                        'betac' : 0.35,
                        'betah' : 2.0 } 
        radouparams.update(extra_params) # Update main parameter set from kwargs
        super(Radou, self).__init__(**radouparams)

    def __str__(self):
        """Print the method name"""
        return 'Radou'

    def __add__(self, other):
        """Sum results in other method object to this one, weighted by number of frames in each"""
        if isinstance(other, Radou):
            try:
                if np.array_equal(self.rates, other.rates):
                    self.pfs[:,0] = (self.n_frames * self.pfs[:,0]) + (other.n_frames * other.pfs[:,0])
                    # SD = sqrt((a^2 * var(A)) + (b^2 * var(B)))
                    self.pfs[:,1] = np.sqrt((self.n_frames**2 * self.pfs[:,1]**2) + (other.n_frames**2 * other.pfs[:,1]**2))
                    self.n_frames += other.n_frames
                    self.pfs[:,0] /= self.n_frames
                    # SD = sd(A)/a
                    self.pfs[:,1] /= self.n_frames
                    self.pf_byframe = np.concatenate((self.pf_byframe, other.pf_byframe), axis=1)
                    self.resfracs = self.dfrac(write=False)
                    return self
                else:
                    raise Functions.HDX_Error("Cannot sum two method objects with different intrinsic rates.")
            except AttributeError:
                return self
        else:
            return self

    def calc_contacts(self, qidx, cidx, cutoff):
        """Calculates contacts between 'query' and 'contact' atom selections
           within a specified cutoff (in nm).
           Periodicity is included in MDtraj function by default.
           Usage: calc_contacts(qidx, cidx, cutoff).

           Qidx and cidx are the atom index lists to search for contacts from
           and to respectively (e.g. from amide NH to all heavy atoms).

           Returns count of contacts for each frame in trajectory Radou.t."""

        try:
            byframe_ctacts = md.compute_neighbors(self.t, cutoff, qidx, haystack_indices=cidx)
        except TypeError:
#            print("Now calculating contacts to single atom, idx %d" % qidx)
            qidx = np.array([qidx])
            byframe_ctacts = md.compute_neighbors(self.t, cutoff, qidx, haystack_indices=cidx)
        return map(lambda x: len(x), byframe_ctacts)

    def _calc_hbonds_contacts(self, HN):
        """Calculates number of protein H-bonds for a particular atom index
           using the 'contacts' method. Bonds to all protein O* or N* evaluated
           by default, optionally all non-protein too (including waters) if 
           Radou.params['protonly'] is False.
       
           Usage: _calc_hbonds_contacts(atom)"""

        # Get N index in same residue as current HN atom
        getN4H = lambda _: self.top.atom(_).residue.atom('N').index
        if self.params['protonly']:
            c = self.top.select("protein and (symbol O or symbol N) and not index %s" % getN4H(HN))
        else:
            c = self.top.select("(symbol O or symbol N) and not index %s" % getN4H(HN))

        hbond_counts = self.calc_contacts(HN, c, self.params['cut_Nh'])
        return hbond_counts

    def _calc_hbonds_bh(self, HN, minfreq=0.0):
        """Calculates number of protein H-bonds for a particular atom index
           using the 'Baker-Hubbard' method. Default donor-acceptor distance < 0.25 nm
           + angle > 120 degrees in Radou.params.
           Reports all H-bonds (minimum freq=0.0) by default. Bonds to all protein 
           O* or N* evaluated by default, optionally all non-protein too 
           (including waters) if Radou.params['protonly'] is False.
       
           Usage: _calc_hbonds_bh(atom, [minfreq])
           Returns: n_frames length array of H-bond counts for desired atom"""

        # Atoms for H-bonds includes protein or all O*, N* and single HN hydrogen

        if self.params['protonly']:
            c = self.t.atom_slice(self.top.select("protein and (symbol O or symbol N) or index %s" % HN))
        else:
            c = self.t.atom_slice(self.top.select("(symbol O or symbol N) or index %s" % HN))

        # Call internal functions of md.baker_hubbard directly to return
        # distances & angles, otherwise only bond_triplets averaged across
        # a trajectory are returned
        bond_triplets = md.geometry.hbond._get_bond_triplets(c.topology, exclude_water=self.params['protonly'])
        mask, distances, angles = md.geometry.hbond._compute_bounded_geometry(c, bond_triplets,\
                                  self.params['bh_dist'], [1, 2], [0, 1, 2], freq=minfreq, periodic=True)

        # can select distance/angle criteria here
        try:
            ang_rad = 2.0*np.pi / (360./self.params['bh_ang'])
        except ZeroDivisionError:
            
            self.params['bh_ang'] = 360.0
            ang_rad = 2.0*np.pi / (360./self.params['bh_ang'])

        hbond_counts = np.sum(np.logical_and(distances < self.params['bh_dist'], angles > ang_rad), axis=1)
        return hbond_counts

    def calc_hbonds(self, donors):
        """Calculates H-bond counts per frame for each atom in 'donors' array
           to each acceptor atom in the system. H-bonds can be defined using
           any one of the methods below, selected with Radou.params['hbond_method']
    
           Available methods:
              'contacts' : Distance-based cutoff of 0.24 nm 
              'bh'       : Baker-Hubbard distance ( < 0.25 nm) and angle ( > 120 deg) cutoff

           Default cutoff/angle can be adjusted with entries 'cut_Nh'/'bh_dist'/
           'bh_ang'in Radou.params.

           Usage: calc_hbonds(donors)
           Returns: n_donors * n_frames 2D array of H-bond counts per frame for all donors"""

    # Switch for H-bond methods
        methods = {
                   'contacts' : self._calc_hbonds_contacts,
                   'bh' : self._calc_hbonds_bh
                  }

        if self.params['skip_first']:
            donors = donors[1:] # Remove first atom/residue from list

        try:
            total_counts = np.zeros((len(donors), self.t.n_frames))
        except TypeError:
            total_counts = np.zeros((1, self.t.n_frames))
        for i, v in enumerate(donors):
            total_counts[i] = methods[self.params['hbond_method']](v)

        reslist = [ self.top.atom(a).residue.index for a in donors ]
#        hbonds = np.concatenate((np.asarray([reslist]).reshape(len(reslist),1), total_counts), axis=1) # Array of [[ Res idx, Contact count ]]

        return np.asarray(reslist), total_counts

    def calc_nh_contacts(self, reslist):
        """Calculates contacts between each NH atom and the surrounding heavy atoms,
           excluding those in residues n-2 to n+2.
    
           By Radou.params default contacts < 0.65 nm are calculated, and only
           protein-heavys, are included, but can include all heavys if desired.
           Also skips first residue (N-terminus) in a residue list by default too
           - see Radou.params['protonly'] and Radou.params['skip_first']

           Usage: calc_nh_contacts(reslist)
           Returns: (reslist, n_res x n_frames 2D array of contacts per frame for each residue)"""

        # Check if current atom is a heavy atom
        is_heavy = lambda _: self.top.atom(_).element.symbol is not 'H'

        if self.params['skip_first']:
            reslist.pop(0) # Remove first atom/residue from list

        contact_count = np.zeros((len(reslist), self.t.n_frames))
        for idx, res in enumerate(reslist):
            robj = self.top.residue(res)
            excl_idxs = range(robj.index - 2, robj.index + 3, 1) # Exclude n-2 to n+2 residues

            inv_atms = Functions.select_residxs(self.t, excl_idxs, protonly=self.params['protonly'], invert=True) # At this stage includes H + heavys
            heavys = inv_atms[ np.array( [ is_heavy(i) for i in inv_atms ] ) ] # Filter out non-heavys

            contact_count[idx] = self.calc_contacts(robj.atom('N').index, heavys, cutoff=self.params['cut_Nc'])

#        contacts = np.concatenate((np.asarray([reslist]).reshape(len(reslist),1), contact_count), axis=1) # Array of [[ Res idx, Contact count ]]
        return np.asarray(reslist), contact_count

    def PF(self):
        """Calculates Radou et al. protection factors for a provided trajectory.
           Empirical scaling factors of Nh * betah and Nc * betac taken from 
           Radou.params (2.0 & 0.35 respectively by default).
           H-bonds can be calculated using either the 'contacts' definition or
           the Baker-Hubbard distance + angle definition. Printout of temporary
           files containing by-residue contacts can be enabled/disabled with 
           Radou.params['save_detailed'].

           All proline residues and the N-terminal residue are skipped. See 
           calc_hbonds and calc_nh_contacts for optional kwargs.       

           Usage: PF()
       
           Returns: (array of residue indices,
                     array of mean protection factors & standard deviations thereof,
                     array of by-frame protection factors for each residue)"""

        # Setup residue/atom lists        
        hn_atms = Functions.extract_HN(self.t, log=self.params['logfile'])
        prolines = Functions.list_prolines(self.t, log=self.params['logfile'])
        # Check all hn_atoms are from protein residues except prolines
        reslist = [ self.top.atom(a).residue.index for a in hn_atms if self.top.atom(a).residue.is_protein and self.top.atom(a).residue.index not in prolines[:,1] ]

        # Calc Nc/Nh
        hres, hbonds = self.calc_hbonds(hn_atms)
        cres, contacts = self.calc_nh_contacts(reslist)

        if not np.array_equal(hres, cres):
            raise Functions.HDX_Error("The residues analysed for Nc and Nh appear to be different. Check your inputs!")

        # Option to save outputs
        if self.params['save_detailed']:
            for i, residx in enumerate(hres):
                with open("Hbonds_%d.tmp" % self.top.residue(residx).resSeq, 'ab') as hbond_f:
                    np.savetxt(hbond_f, hbonds[i], fmt='%d') # Use residue indices internally, print out IDs
            for i, residx in enumerate(cres):
                with open("Contacts_%d.tmp" % self.top.residue(residx).resSeq, 'ab') as contacts_f:
                    np.savetxt(contacts_f, contacts[i], fmt='%d') # Use residue indices internally, print out IDs
        # Calc PF with phenomenological equation
        hbonds *= self.params['betah']     # Beta parameter 1
        contacts *= self.params['betac']   # Beta parameter 2
    
        pf_byframe = np.exp(hbonds + contacts)
        pf_bar = np.mean(pf_byframe, axis=1)
        pf_bar = np.stack((pf_bar, np.std(pf_byframe, axis=1, ddof=1)), axis=1)
        rids = np.asarray([ self.top.residue(i).resSeq for i in hres ])
        rids = np.reshape(rids, (len(rids), 1))
        # Save PFs to separate log file, appending filenames for trajectories read as chunks
        if os.path.exists(self.params['outprefix']+"Protection_factors.dat"):
            filenum = len(glob.glob(self.params['outprefix']+"Protection_factors*"))
            np.savetxt(self.params['outprefix']+"Protection_factors_chunk_%d.dat" % (filenum+1), \
                       np.concatenate((rids, pf_bar), axis=1), fmt=['%7d', '%18.8f', '%18.8f'], \
                       header="ResID  Protection factor Std. Dev.") # Use residue indices internally, print out IDs
        else:    
            np.savetxt(self.params['outprefix']+"Protection_factors.dat", np.concatenate((rids, pf_bar), axis=1), \
                       fmt=['%7d', '%18.8f', '%18.8f'], header="ResID  Protection factor Std. Dev.") # Use residue indices internally, print out IDs

        return hres, pf_bar, pf_byframe

    def run(self, trajectory):
        """Runs a by-residue HDX prediction for the provided MDTraj trajectory

           Usage: run(traj)
           Returns: None (results are stored as Radou.resfracs)"""
        self.t = trajectory # Note this will add attributes to the original trajectory, not a copy
        self.n_frames = self.t.n_frames
        self.top = trajectory.topology.copy() # This does not add attributes to the original topology
        self.assign_cis_proline()
        self.assign_disulfide()
        self.assign_his_protonation()
        self.assign_termini()
        self.reslist, self.pfs, self.pf_byframe = self.PF()
                                   
        self.rates = self.kint()
        self.resfracs = self.dfrac()
        print("Residue predictions complete")

### Add further classes for methods below here

class PH(DfPred.DfPredictor):
    """Class for Persson-Halle style analysis. PNAS, 2015, 112(33), 10383-10388.
       Subclass of DfPredictor. Initialises with a dictionary of default
       parameters for analysis, accessible as PH.params

       Default parameters can either be updated directly in the PH.params
       dictionary or by supplying a extra parameters as kwargs during
       initialisation, e.g.: PH() or PH(**param_dict)

       Run a by-residue deuterated fraction prediction with these parameters
       using the PH.run method."""

    def __init__(self, **extra_params):
        """Initialise parameters for Persson-Halle-style analysis.
           See self.params for default values"""
        # Initialise main parameters with defaults
        phparams = { 'cut_O' : 0.26 }
        phparams.update(extra_params) # Update main parameter set from kwargs
        super(PH, self).__init__(**phparams)

    def __str__(self):
        """Print the method name"""
        return 'Persson-Halle'

    def __add__(self, other):
        """Sum results in other method object to this one, weighted by number of frames in each"""
        if isinstance(other, PH):
            try:
                if np.array_equal(self.rates, other.rates):
                    self.n_frames += other.n_frames
                    self.watcontacts = np.concatenate((self.watcontacts, other.watcontacts), axis=1)
                    self.pf_byframe = np.concatenate((self.pf_byframe, other.pf_byframe), axis=1)
                    self.PF(update_only=True)
                    self.resfracs = self.dfrac(write=False)
                    return self
                else:
                    raise Functions.HDX_Error("Cannot sum two method objects with different intrinsic rates.")
            except AttributeError:
                return self
        else:
            return self

    def calc_wat_contacts(self, hn_atms):
        """Calculate water contacts for each frame and residue in the trajectory"""

        solidxs = self.top.select("water and element O") 
        

        if self.params['skip_first']:
            hn_atms = hn_atms[1:]

        reslist = [ self.top.atom(i).residue.index for i in hn_atms ]
        contacts = np.zeros((len(reslist), self.t.n_frames))
        for idx, hn in enumerate(hn_atms):
            contacts[idx] = map(len, md.compute_neighbors(self.t, self.params['cut_O'], \
                                           np.asarray([hn]), haystack_indices=solidxs))
            if self.params['save_detailed']:
                with open("Waters_%d.tmp" % self.top.atom(hn).residue.resSeq, 'ab') as wat_f:
                    np.savetxt(wat_f, contacts[idx], fmt='%d')

        return np.asarray(reslist), contacts

 
    def PF(self, update_only=False):


        if not update_only:
            hn_atms = Functions.extract_HN(self.t, log=self.params['logfile'])
            prolines = Functions.list_prolines(self.t, log=self.params['logfile'])
            # Check all hn_atoms are from protein residues except prolines
            protlist = np.asarray([ self.top.atom(a).residue.index for a in hn_atms if self.top.atom(a).residue.is_protein and self.top.atom(a).residue.index not in prolines[:,1] ])

            self.reslist, self.watcontacts = self.calc_wat_contacts(hn_atms)
            if self.params['skip_first']:
                if not np.array_equal(protlist[1:], self.reslist):
                    raise Functions.HDX_Error("One or more residues analysed for water contacts is either proline or a non-protein residue. Check your inputs!")
            else:
                if not np.array_equal(protlist, self.reslist):
                    raise Functions.HDX_Error("One or more residues analysed for water contacts is either proline or a non-protein residue. Check your inputs!")

        # Update/calculation of PF
        opencount, closedcount = np.sum(self.watcontacts > 1, axis=1), np.sum(self.watcontacts <= 1, axis=1)
        with np.errstate(divide='ignore'):
            self.pfs = closedcount/opencount # Ignores divide by zero
#            self.pfs[np.isinf(self.pfs)] = self.n_frames
        if not update_only:
            self.pf_byframe = np.repeat(np.atleast_2d(self.pfs).T, self.n_frames, axis=1)
        self.pfs = np.stack((self.pfs, np.zeros(len(self.watcontacts))), axis=1)
        
    def run(self, trajectory):
        """Runs a by-residue HDX prediction for the provided MDTraj trajectory

           Usage: run(traj)
           Returns: None (results are stored as PH.resfracs)"""
        self.t = trajectory # Note this will add attributes to the original trajectory, not a copy
        self.n_frames = self.t.n_frames
        self.top = trajectory.topology.copy() # This does not add attributes to the original topology
        self.assign_cis_proline()
        self.assign_disulfide()
        self.assign_his_protonation()
        self.assign_termini()
        self.PF()

        self.rates = self.kint()
        self.resfracs = self.dfrac()
        print("Residue predictions complete")
