
'''
StatisticalModels.py - Set of functions for building statistical models, calculations and tests.

Classes
-------

Functions
---------

Dependancies
------------


'''

import numpy as np
import pandas as pd
import numpy
import scipy.interpolate as interp
import scipy.integrate as integrate
import scipy.optimize as op
import scipy.special as spec
import emcee
#from skopt import gp_minimize
import sys, os, time
from mpmath import *

# Import cubature for integrating over regions
#from cubature import cubature


class GaussianEM():

    '''
    GaussianEM - Class for calculating bivariate Gaussian mixture model which best fits
                 the given poisson point process data.

    Parameters
    ----------
        x, y - np.array of floats
            - x and y coordinates for the points generated via poisson point process from
            the smooth model

        nComponents - int
            - Number of Gaussian components of the mixture model

        rngx, rngy - tuple of floats
            - Upper and lower bounds on x and y of the survey

    Functions
    ---------
        __call__ - Returns the value of the smooth GMM distribution at the points x, y
        optimizeParams - Vary the parameters of the distribution using the given method to optimize the
                        poisson likelihood of the distribution
        initParams - Specify the initial parameters for the Gaussian mixture model as well as
                    the lower and upper bounds on parameters (used as prior values in the optimization)
        lnprob - ln of the posterior probability of the distribution given the parameters.
               - posterior probability function is proportional to the prior times the likelihood
               - lnpost = lnprior + lnlike
        lnlike - The poisson likelihood disrtibution of the Gaussian mixture model given the observed points
        lnprior - The test of the parameters of the Gaussian Mixture Model against the specified prior values
        priorTest - Testing the parameters of the GMM against the upper and lower limits specified in
                    self.initParams
        testIntegral - Test the approximate integral calculated using the given integration rule against the accurate
                        integral calculated using cubature for which we know the uncertainty
    '''

    def __init__(self, x=np.zeros(0), y=np.zeros(0), sig_xy=None,
                nComponents=0, rngx=(0,1), rngy=(0,1), runscaling=True, runningL=True, s_min=0.1,
                photoDF=None, priorDF=False):

        # Iteration number to update
        self.iter_count = 0

        # Name of the model to used for reloading from dictionary
        self.modelname = self.__class__.__name__

        # Distribution from photometric survey for calculation of SF
        self.photoDF = photoDF
        self.priorDF = priorDF

        # Number of components of distribution
        self.nComponents = nComponents
        # Starting values for parameters
        self.params_i = None
        # Prior limits on parameters
        self.params_l, self.params_u = None, None
        # Final optimal values for parameters
        self.params_f = None
        # Shape of parameter set (number of components x parameters per component)
        self.param_shape = ()

        # Boundary on minimum std
        self.s_min=s_min

        # Method used for integration
        self.integration='trapezium'

        # Coordinate covariance matrix
        if sig_xy is None:
            z_ = np.zeros(len(x))
            sig_xy = np.array([[z_, z_],[z_, z_]]).transpose(2,0,1)
        self.sig_xy = sig_xy

        self.runscaling = runscaling
        # Not run when loading class from dictionary
        if runscaling:
            # Real space parameters
            self.x = x.copy()
            self.y = y.copy()
            self.rngx, self.rngy = rngx, rngy
            # Statistics for feature scaling
            if len(x)>1:
                self.mux, self.sx = np.mean(x), np.std(x)
                self.muy, self.sy = np.mean(y), np.std(y)
            else:
                # SD=0 if only one point which causes problems!
                self.mux, self.sx = np.mean(x), (rngx[1]-rngx[0])/4
                self.muy, self.sy = np.mean(y), (rngy[1]-rngy[0])/4
            # Scaled parameters
            self.x_s, self.y_s = feature_scaling(x, y, self.mux, self.muy, self.sx, self.sy)
            self.rngx_s, self.rngy_s = feature_scaling(np.array(rngx), np.array(rngy), self.mux, self.muy, self.sx, self.sy)
            self.sig_xy_s = covariance_scaling(self.sig_xy, self.sx, self.sy)
        else:
            self.x_s, self.y_s = x, y
            self.rngx_s, self.rngy_s = rngx, rngy
            self.sig_xy_s = sig_xy

        # Function which calculates the actual distribution
        self.distribution = bivGaussMix_vect

        # Print out likelihood values as calculated
        self.runningL = runningL

        Nx_int, Ny_int = (250,250)
        x_coords = np.linspace(self.rngx_s[0], self.rngx_s[1], Nx_int)
        y_coords = np.linspace(self.rngy_s[0], self.rngy_s[1], Ny_int)
        self.x_2d, self.y_2d = np.meshgrid(x_coords, y_coords)

        if self.priorDF:
            # Calculate Gaussian distributions from product of scaled DF and scaled star positions
            self.params_df = self.scaleParams(self.photoDF.params_f, dfparams=True)
            function = lambda a, b: self.distribution(self.params_df, a, b)
            #if self.runningL:
            #    print 'DF integral = ', numericalIntegrate_precompute(function, self.x_2d, self.y_2d)
            self.ndf = len(self.photoDF.x)


        else: self.ndf = None

    def __call__(self, x, y, components=None, params=None):

        '''
        __call__ - Returns the value of the smooth GMM distribution at the points x, y

        Parameters
        ----------
            x, y - float or np.array of floats
                - x and y coordinates of points at which to take the value of the GMM
                - From input - x is magnitude, y is colour

            components=None:
                - List of components to check for distribution values

            params=None:
                - The parameters on which the model will be evaluatedself.
                - If None, params_f class attribute will be used

        Returns
        -------
            GMMval: float or np.array of floats
                - The value of the GMM at coordinates x, y
        '''
        #
        if params is None: params=self.params_f.copy()

        # Scale x and y to correct region - Currently done to params_f - line 371  - but could change here instead
        #x, y = feature_scaling(x, y, self.mux, self.muy, self.sx, self.sy)
        #rngx, rngy = feature_scaling(np.array(self.rngx), np.array(self.rngy), self.mux, self.muy, self.sx, self.sy)
        rngx, rngy = np.array(self.rngx), np.array(self.rngy)

        # Value of coordinates x, y in the Gaussian mixture model
        if components is None: components = np.arange(self.nComponents)
        GMMval = self.distribution(params[components, :], x, y)

        if (type(GMMval) == np.array)|(type(GMMval) == np.ndarray)|(type(GMMval) == pd.Series):
            # Not-nan input values
            notnan = (~np.isnan(x))&(~np.isnan(y))
            # Any values outside range - 0
            constraint = (x[notnan]>=rngx[0])&(x[notnan]<=rngx[1])&(y[notnan]>=rngy[0])&(y[notnan]<=rngy[1])
            GMMval[~notnan] = np.nan
            GMMval[notnan][~constraint] = 0.
        elif (type(GMMval) == float) | (type(GMMval) == np.float64):
            if (np.isnan(x))|(np.isnan(y)): GMMval = np.nan
            else:
                constraint = (x>=rngx[0])&(x<=rngx[1])&(y>=rngy[0])&(y<=rngy[1])
                if not constraint:
                    GMMval = 0.
        else: raise TypeError('The type of the input variables is '+str(type(GMMval)))

        return GMMval

    def optimizeParams(self, method="Powell", init="random"):

        '''
        optimizeParams - Initialise and optimize parameters of Gaussian mixture model.

        **kwargs
        --------
            method = "Powell" - str
                - The scipy.optimize.minimize method used to vary the parameters of the distribution
                in order to find the minimum negative log likelihooh (min ( -log(L) ))

        Returns
        -------
            result - dict
                - Output of scipy.optimize.minimise showing the details of the process
        '''

        # Set initial parameters
        finite = False
        a = 0
        if self.runningL: print 'init, ', init

        while not finite:
            if not init=="reset":
                self.params_i, self.params_l, self.params_u = \
                    initParams(self.nComponents, self.rngx_s, self.rngy_s, self.priorDF,
                                    nstars=len(self.x_s), ndf=self.ndf, runscaling=self.runscaling, l_min=self.s_min)
            if init=="kmeans":
                # K-Means clustering of initialisation
                if not self.priorDF: params_km = kmeans(np.vstack((self.x_s, self.y_s)).T, self.nComponents)
                elif self.priorDF:
                    weights = 1/self.distribution(self.params_df, self.x_s, self.y_s)
                    params_km = kmeans(np.vstack((self.x_s, self.y_s)).T, self.nComponents, weights=weights, ndf=self.ndf)
                self.params_i[:,[0,1,5]] = params_km[:,[0,1,5]]
                self.params_i[self.params_i<self.params_l] = self.params_l[self.params_i<self.params_l]*1.01
                self.params_i = self.params_i[self.params_i[:,0].argsort()]

            if init=='reset':
                # Using current final parameters to initialise parameters
                params = self.params_f_scaled
            else:
                params = self.params_i.copy()

            if self.runningL: print 'initial parameters', params
            self.param_shape = params.shape
            lnp = self.lnprob(params)
            finite = np.isfinite(lnp)
            a+=1
            if a%3==0:
                raise ValueError("Couldn't initialise good parameters")
            if not finite:
                print "Fail: ", self.params_i,\
                        prior_bounds(self.params_i, self.params_l, self.params_u),\
                        prior_multim(self.params_i),\
                        prior_erfprecision(self.params_i, self.rngx_s, self.rngy_s), \
                        len(self.x_s)

        start = time.time()
        bounds = None
        # Run scipy optimizer
        if self.runningL: print("\nInitparam likelihood: %.2f" % float(self.lnprob(params)))
        paramsOP = self.optimize(params, method, bounds)
        # Check likelihood for parameters
        lnlikeOP = self.lnlike(paramsOP)
        if self.runningL: print("\n %s: lnprob=%.0f, time=%d" % (method, self.lnprob(paramsOP), time.time()-start))
        params=paramsOP

        if not method=='emceeBall':
            self.params_f_scaled = params.copy()
            if self.runscaling: params = self.unscaleParams(params)
            # Save evaluated parameters to internal values
            self.params_f = params.copy()

        return params

    def optimize(self, params, method, bounds):

        '''
        optimize - Run scipy.optimize.minimize to determine the optimal parameters for the distribution

        Parameters
        ----------
            params: array of float
                - Initial parameters of the distribution

            method: str
                - Method to be used by optimizer, e.g. "Powell"

            bounds: list of tup of float or None
                - Boundaries of optimizing region
                - Only for optimization methods which take bounds

        Returns
        -------
            result: dict
                - Output of scipy.optimize
        '''

        # Set count to 0
        self.iter_count = 0

        # To clean up any warnings from optimize
        invalid = np.seterr()['invalid']
        divide = np.seterr()['divide']
        over = np.seterr()['over']
        np.seterr(invalid='ignore', divide='ignore', over='ignore')

        if type(method) is str:
            if method=='Stoch': optimizer = scipyStoch
            elif method=='Powell': optimizer = scipyOpt
            elif method=='Anneal': optimizer = scipyAnneal
            elif method=='emceeBall': optimizer = lambda a, b: emcee_ball(a, b, params_l=self.params_l, params_u=self.params_u)
            else: raise ValueError('Name of method not recognised.')
        else:
            optimizer = method
        kwargs = {'method':method, 'bounds':bounds}
        # result is the set of theta parameters which optimize the likelihood given x, y, yerr
        test = self.nll(params)
        if self.runningL: print r'\nInit  lnl: ', test, r'\n'
        params, self.output = optimizer(self.nll, params)#, pl = self.params_l[0,:], pu = self.params_u[0,:])
        params = params.reshape(self.param_shape)
        # Potential to use scikit optimize
        #bounds = list(zip(self.params_l.ravel(), self.params_u.ravel()))
        #result = gp_minimize(self.nll, bounds)

        # To clean up any warnings from optimize
        np.seterr(invalid=invalid, divide=divide, over=over)
        if self.runningL: print("")

        return params

    def nll(self, *args):

        """
        nll - Negative log likelihood for use in the optimizer
        """
        lp = -self.lnprob(*args)
        return lp

    def lnprob(self, params):

        '''
        lnprob - ln of the posterior probability of the distribution given the parameters.
               - posterior probability function is proportional to the prior times the likelihood
               - lnpost = lnprior + lnlike

        Parameters
        ----------
            params: array of float
                - Initial parameters of the distribution

        Returns
        -------
            lnprior(params)+lnlike(params) - float
                - ln of the posterior probability
                - -np.inf if the prior is false and hence prob=0 - lnprob=-inf
        '''

        # Reshape parameters for testing
        params = params.reshape(self.param_shape)

        lp = self.lnprior(params)

        # if prior not satisfied, don't calculate lnlike
        if not np.isfinite(lp): return -np.inf
        # if prior satisfied
        else: return lp + self.lnlike(params)

    def lnlike(self, params):

        '''
        lnlike - The poisson likelihood disrtibution of the Gaussian mixture model given the observed points

        Parameters
        ----------
            params: array of float
                - Initial parameters of the distribution

        Returns
        -------
            contPoints-contInteg - float
                - lnL for the GMM parameters given the observed points
        '''

        # If the DF has already been calculated, directly optimize the SF
        if self.priorDF:
            # Create copies to prevent overwrite problems
            params1, params2 = params.copy(), self.params_df.copy()
            function = lambda a, b: self.distribution(params1, a, b) \
                                    * self.distribution(params2, a, b)
            #model = self.norm_df_spec*error_convolution(params, self.mu_df_spec[:,0], self.mu_df_spec[:,1], self.sig_df_spec)
            params = gmm_product_p(params, self.params_df)
            params = params.reshape(-1, params.shape[-1])
            model = error_convolution(params, self.x_s, self.y_s, self.sig_xy_s)
        else:
            function = lambda a, b: self.distribution(params, a, b)
            model = error_convolution(params, self.x_s, self.y_s, self.sig_xy_s)
            #model = self.distribution(params, self.x_s, self.y_s)

        # Point component of poisson log likelihood: contPoints \sum(\lambda(x_i))
        #model = function(*(self.x_s, self.y_s))
        contPoints = np.sum( np.log(model) )

        # Integral of the smooth function over the entire region
        contInteg = integrationRoutine(function, params, self.nComponents, self.rngx_s, self.rngy_s,
                                        self.x_2d, self.y_2d, integration=self.integration)
        #print bivGauss_analytical_approx(params, self.rngx_s, self.rngy_s), self.rngx_s, self.rngy_s

        lnL = contPoints - contInteg
        if self.runningL:
            sys.stdout.write("\ritern: %d, logL: %.2f, sum log(f(xi)): %.2f, integral: %.2f                " \
                            % (self.iter_count, lnL, contPoints, contInteg))
            sys.stdout.flush()
            self.iter_count += 1

        return lnL

    def lnprior(self, params):

        '''
        lnprior - The test of the parameters of the Gaussian Mixture Model against the prior

        Parameters
        ----------
            params: array of float
                - Initial parameters of the distribution

        Returns
        -------
            lnprior - either 0.0 or -np.inf
                - If the prior is satisfied, p=1. therefore lnp = 0.0
                - If not satisfied, p=0. therefore lnp = -np.inf
        '''

        # Test parameters against boundary values
        prior = prior_bounds(params, self.params_l, self.params_u)
        if not prior: return -np.inf

        # Test parameters against variance
        #if prior: prior = prior & sigma_bound(params, self.s_min)
        #if not prior: return -np.inf

        # Test parameters against boundary values
        prior = prior_multim(params)
        if not prior: return -np.inf

        # Test parameters against boundary values
        if self.integration=='analyticApprox':
            prior = prior_erfprecision(params, self.rngx_s, self.rngy_s)
            if not prior: return -np.inf

        # Prior on spectro distribution that it must be less than the photometric distribution
        if self.priorDF:
            function = lambda a, b: self.distribution(params, a, b)
            prior = prior & SFprior(function, self.x_2d, self.y_2d)
            if not prior: return -np.inf

        # All prior tests satiscied
        return 0.0

    def scaleParams(self, params_in, dfparams=False):

        params = params_in.copy()
        params[:,[0,1]] = (params[:,[0,1]] -  [self.mux, self.muy]) / np.array([self.sx, self.sy])

        sigma = np.array([np.diag(a) for a in params[:,2:4]])
        R = rotation(params[:,4])
        sigma = np.matmul(R, np.matmul(sigma, R.transpose(0,2,1)))

        sigma[:,[0,1],[0,1]] *= 1/np.array([self.sx**2, self.sy**2])
        sigma[:,[0,1],[1,0]] *= 1/(self.sx*self.sy)

        eigvals, eigvecs = np.linalg.eig(sigma)
        # Line eigvecs up with eigvals
        i = eigvals.argsort(axis=1)
        j = np.repeat([np.arange(eigvals.shape[0]),], eigvals.shape[1], axis=0).T
        eigvecs = eigvecs[j, i, :]
        eigvals = eigvals[j, i]

        params[:,[2,3]] = eigvals #np.sort(eigvals, axis=1)
        th = np.arctan2(eigvecs[:,0,1], eigvecs[:,0,0])
        params[:,4] = th

        if self.priorDF & (not dfparams):
            params[:,5] /= (self.sx*self.sy)

        return params

    def unscaleParams(self, params_in, dfparams=False):

        params = params_in.copy()
        params[:,[0,1]] = (params[:,[0,1]] * np.array([self.sx, self.sy])) +  [self.mux, self.muy]

        sigma = np.array([np.diag(a) for a in params[:,2:4]])
        R = rotation(params[:,4])
        sigma = np.matmul(R, np.matmul(sigma, R.transpose(0,2,1)))

        sigma[:,[0,1],[0,1]] *= np.array([self.sx**2, self.sy**2])
        sigma[:,[0,1],[1,0]] *= self.sx*self.sy

        eigvals, eigvecs = np.linalg.eig(sigma)

        i = eigvals.argsort(axis=1)
        j = np.repeat([np.arange(eigvals.shape[0]),], eigvals.shape[1], axis=0).T
        eigvecs = eigvecs[j, i, :]
        eigvals = eigvals[j, i]

        params[:,[2,3]] = eigvals #np.sort(eigvals, axis=1)
        th = np.arctan2(eigvecs[:,0,1], eigvecs[:,0,0])
        params[:,4] = th

        if self.priorDF & (not dfparams):
            params[:,5] *= (self.sx*self.sy)

        return params

    def testIntegral(self, integration='trapezium'):

        '''
        testIntegral - Test the approximate integral calculated using the given integration rule against the accurate
                        integral calculated using cubature for which we know the uncertainty

        **kwargs
        --------
            integration='trapezium' - str
                - The type of integration routine to be tested
                - 'analytic', 'trapezium', 'simpson', 'cubature'

        Returns
        -------
            calc_val - float
                - integral calculated using trapezium rule approximation

            real_val - float
                - integral calculated using cubature routine

            err - float
                - The error of the calculated value with respect to the real value

        Also prints out the values and errors of each calculation automatically
        '''

        function = lambda a, b: self.distribution(self.params_f, a, b)
        cub_func = lambda X: self.distribution(self.params_f, X[0], X[1])

        real_val, err = cubature(function, 2, 1, (self.rngx_s[0], self.rngy_s[0]), (self.rngx_s[1], self.rngy_s[1]))
        calc_val = integrationRoutine(function, self.params_f, self.nComponents, *(self.rngx_s, self.rngy_s), integration=integration)

        percent = ((calc_val - float(real_val))/calc_val)*100
        cubature_percent = 100*float(err)/float(real_val)

        print("\nThe error in the linear numerical integral was %.3E%%" % float(percent))
        print("\nThe cubature calculation error is quoted as %.3E or %.3E%%" % (float(err), cubature_percent)  )

        return calc_val, real_val, err

    def stats(self):

        '''
        This needs updating!!

        stats - Prints out the statistics of the model fitting.

        Such as parameters, likelihoods, integration calculations

        Inherited
        ---------
            params_f: arr of float
                - Final parameter values after optimization

            rngx_s, rngy_s: tuple of float
                - Feature scaled x and y range

        Returns
        -------
            None
        '''

        print("Parameters:")
        for i in range(self.params_f.shape[0]):
            print("Parameters for component %d:" % i)
            mu = np.array((self.params_f[i,0], self.params_f[i,2]))
            sigma = np.array([[self.params_f[i,1], self.params_f[i,5]], [self.params_f[i,5], self.params_f[i,3]]])

            X = np.vstack((self.x_s.ravel(), self.y_s.ravel())).T
            p = self.params_f[i,4]*bivariateGaussVector(X, mu, sigma)

            print("mu x and y: {}, {}".format(*mu))
            print("covariance matrix: {}".format(str(sigma)))
            print("Weight: {}".format(self.params_f[i,4]))
            print("Contribution: {}.\n".format(p))


        nll = -self.lnprob(self.params_f)
        print("Negative log likelihood: {:.3f}".format(nll))
        lnlike = self.lnlike(self.params_f)
        print("Log likelihood: {:.3f}".format(lnlike))
        lnprior = self.lnprior(self.params_f)
        print("Log prior: {:.3f}".format(lnprior))
        #int_calc, int_real, err = self.testIntegral()
        #print("Integration: Used - {:.3f}, Correct - {:.3f} (Correct undertainty: {:.2f})".format(int_calc, int_real, err))
        function = lambda a, b: self.distribution(self.params_f, a, b)
        calc_val = numericalIntegrate(function, *(self.rngx_s, self.rngy_s))
        calc_val2 = numericalIntegrate(function, *(self.rngx_s, self.rngy_s), Nx_int=500, Ny_int=500)
        print("Integration: Used {:.3f}, Half spacing {:.3f}".format(calc_val, calc_val2))


def initParams(nComponents, rngx, rngy, priorDF, nstars=1, ndf=None, runscaling=True, l_min=0.1):

    '''
    initParams - Specify the initial parameters for the Gaussian mixture model as well as
                the lower and upper bounds on parameters (used as prior values in the optimization)

    Parameters
    ----------
        nComponents: int
            - Number of components of the Gaussian Mixture model

        rngx, rngy: tuple of float
            - Range of region in x and y axis

        priorDF: bool
            - Is there a prior distribution function?
            - True if calculating for selection function

    Returns
    -------
        params_i - array of floats
            - initial parameters for the Gaussian mixture model
        params_l - array of floats
            - lower bounds on the values of GMM parameters
        params_u - array of floats
            - upper bounds on the values of GMM parameters
    '''


    # Initial guess parameters for a bivariate Gaussian
    # Randomly initialise mean between -1 and 1
    mux_i, muy_i = np.random.rand(2, nComponents)
    mux_i = mux_i * (rngx[1]-rngx[0]) + rngx[0]
    muy_i = muy_i * (rngy[1]-rngy[0]) + rngy[0]

    # l_min to allow erf calculation:
    half_diag = np.sqrt((rngx[1]-rngx[0])**2 + (rngy[1]-rngy[0])**2)/2
    #l_min = min(half_diag*np.sqrt(2)/24, np.min((rngx[1]-rngx[0], rngy[1]-rngy[0]))/10)
    #l_min = 0.01
    if priorDF: l_min = np.sqrt((rngx[1]-rngx[0])*(rngy[1]-rngy[0])/nstars)
    if not priorDF: l_min = np.sqrt((rngx[1]-rngx[0])*(rngy[1]-rngy[0])/nstars)/10
    #l_min = half_diag/4
    # Generate initial covariance matrix
    l1_i, l2_i = np.sort(np.random.rand(2, nComponents), axis=0)
    l1_i = l1_i * half_diag + l_min
    l2_i = l2_i * half_diag + l_min

    # Initialise thetas
    th_i = np.random.rand(nComponents) * np.pi

    # Weights sum to 1
    w_l = 1./(5*nComponents) # 5 arbitrarily chosen
    w_i = np.random.rand(nComponents)*(2./nComponents - w_l) + w_l
    w_u = 10. # Arbitrarily chosen
    if priorDF:
        w_l *= float(nstars)/ndf
        w_i *= float(nstars)/ndf
    if not priorDF:
        w_l *= nstars
        w_i *= nstars
        w_u *= nstars

    # Lower and upper bounds on parameters
    # Mean at edge of range
    mux_l, mux_u = rngx[0], rngx[1]
    muy_l, muy_u = rngy[0], rngy[1]
    # Zero standard deviation to inf
    l1_l, l2_l = l_min, l_min
    l1_u, l2_u = np.min((rngx[1]-rngx[0], rngy[1]-rngy[0]))*30, np.max((rngx[1]-rngx[0], rngy[1]-rngy[0]))*30
    #, np.inf, np.inf #l_rng[1], l_rng[1] #

    th_l, th_u = 0, np.pi


    params_i = np.vstack((mux_i, muy_i, l1_i, l2_i, th_i, w_i)).T
    params_l = np.repeat([[mux_l, muy_l, l1_l, l2_l, th_l, w_l],], nComponents, axis=0)
    params_u = np.repeat([[mux_u, muy_u, l1_u, l2_u, th_u, w_u],], nComponents, axis=0)

    params_i = params_i[params_i[:,0].argsort()]

    return params_i, params_l, params_u

def sigma_bound(params, s_min):

    '''
    sigma_bound - Priors placed on the covariance matrix in the parameters

    Parameters
    ----------
        params - array of floats
            - Values of parameters in the Gaussian Mixture model

    Returns
    -------
        - bool
        - True if good covariance matrix, False if not
        - Good covariance has det>0
    '''
    # Construct covariance matrix into nComponent 2x2 arrays
    sigma = np.zeros((params.shape[0],2,2))
    sigma[:,[0,0,1,1],[0,1,0,1]] = params[:, [1,5,5,3]]
    try: eigvals = np.linalg.eigvals(sigma)
    except np.linalg.LinAlgError:
        print(params)
        print(sigma)
        raise ValueError('bad sigma...params:', params, 'sigma:', sigma)

    if np.sum(eigvals<=s_min) > 0: return False
    else: return True

def prior_bounds(params, params_l, params_u):

    '''
    prior_bounds - Testing the parameters of the GMM against the upper and lower limits
        - uninformative prior - uniform and non-zero within a specified range of parameter values

    Parameters
    ----------
        params - array of floats
            - Values of parameters in the Gaussian Mixture model

    Inherited
    ---------
        params_l, params_u: array of float
            - Lower and upper bounds of parameters

    Returns
    -------
         - prior: bool
            - True if parameters satisfy constraints. False if not.
    '''
    # Total is 0 if all parameters within priors
    total = np.sum(params <= params_l) + np.sum(params >= params_u)
    # prior True if all parameters within priors
    prior = total == 0

    return prior

def prior_multim(params):
    """
    prior_multim - Prior on eigenvalues and means of Gaussian mixture models
                    to remove some degenerate solutions (e.g. reordering components)
    """

    # Prior on the order of lambda values in one mixture component
    # Removes degeneracy between eigenvalue and angle
    l_order = np.sum(params[:,2]>params[:,3])
    # Prior on order of components.
    comp_order = np.sum( np.argsort(params[:,0]) != np.arange(params.shape[0]) )
    #comp_order=0
    prior = l_order+comp_order == 0
    #print 'order: ', l_order, comp_order

    return prior

def prior_erfprecision(params, rngx, rngy):

    # shape 2,4 - xy, corners
    corners = np.array(np.meshgrid(rngx, rngy)).reshape(2, 4)
    # shape n,2,1 - components, xy, corners
    angle1 = np.array([np.sin(params[:,4]), np.cos(params[:,4])]).T[:,:,np.newaxis]
    angle2 = np.array([np.sin(params[:,4]+np.pi/2), np.cos(params[:,4]+np.pi/2)]).T[:,:,np.newaxis]
    # shape n,2,1 - components, xy, minmax
    mean = params[:,:2][:,:,np.newaxis]
    # shape n,4 - components, corners
    dl1 = np.sum( (corners - mean)*angle1 , axis=1)
    dl2 = np.sum( (corners - mean)*angle2 , axis=1)
    # shape 2,n,4 - axes, components, corners
    dl = np.stack((dl1, dl2))
    dl.sort(axis=2)
    # shape 2,n,2 - axes, components, extreme corners
    dl = dl[..., 0]

    # shape 2,n,2 - axes, components, extreme corners
    component_stds = params[:,2:4].T

    separation = np.abs(dl) / (np.sqrt(2) * component_stds)

    if np.sum(separation>25) > 0: return False
    else: return True

def SFprior(function, xx, yy):

    '''
    SFprior - The selection function has to be between 0 and 1 everywhere.
        - informative prior

    Parameters
    ----------
        function - function
            - The selection function interpolant over the region, R.

        xx, yy:

    Returns
    -------
        prior - bool
            - True if all points on GMM are less than 1.
            - Otherwise False
    '''

    f_max = np.max( function(*(xx, yy)) )
    prior = not f_max>1

    return prior


def bivGaussMix_vect(params, x, y):

    '''
    bivGaussMix_vect - Calculation of bivariate Gaussian distribution.

    Parameters
    ----------
        params - arr of float - length 6 - [mux, muy, l1, l2, theta, weight]
            - Parameters of the bivariate gaussian
        x, y - arr of float
            - x and y coordinates of points being tested
    Returns
    -------
        p - arr of float
            - bivariate Gaussian value for each point in x, y
    '''

    shape = x.shape
    X = np.vstack((x.ravel(), y.ravel())).T

    mu = params[:,:2]
    sigma = np.array([np.diag(a) for a in params[:,2:4]])
    R = rotation(params[:,4])
    weight= params[:,5]
    sigma = np.matmul(R, np.matmul(sigma, R.transpose(0,2,1)))

    # Inverse covariance
    inv_cov = np.linalg.inv(sigma)
    # Separation of X from mean
    X = np.moveaxis(np.repeat([X,], mu.shape[-2], axis=0), 0, -2) - mu

    # X^T * Sigma
    X_ext = X[...,np.newaxis]
    inv_cov = inv_cov[np.newaxis,...]
    X_cov = X_ext*inv_cov
    X_cov = X_cov[...,0,:]+X_cov[...,1,:]
    # X * Sigma * X
    X_cov_X = X_cov*X
    X_cov_X = X_cov_X[:,:,0]+X_cov_X[:,:,1]
    # Exponential
    e = np.exp(-X_cov_X/2)

    # Normalisation term
    det_cov = np.linalg.det(sigma)
    norm = 1/np.sqrt( ((2*np.pi)**2) * det_cov)

    p = np.sum(weight*norm*e, axis=-1)

    #if np.sum(np.isnan(p))>0: print(params)

    return p.reshape(shape)
def error_convolution(params, x, y, sig_xy):

    shape = x.shape
    X = np.vstack((x.ravel(), y.ravel())).T

    # 1) Rotate S_component to x-y plane
    mu = params[:,:2]
    sigma = np.array([np.diag(a) for a in params[:,2:4]])
    R = rotation(params[:,4])
    weight= params[:,5]
    sigma = np.matmul(R, np.matmul(sigma, R.transpose(0,2,1)))

    # 2) Get Si + Sj for all stars_i, comonents_j
    sigma = sigma[np.newaxis, ...]
    sig_xy = sig_xy[:, np.newaxis, ...]
    sig_product = sigma + sig_xy

    # 3) Get mui - muj for all stars_i, comonents_j
    mu = mu[np.newaxis, ...]
    X = X[:, np.newaxis, ...]
    mu_product = mu - X

    # 4) Calculate Cij
    sig_product_inv, sig_product_det = inverse2x2(sig_product)
    # np.einsum('ijlm, ijm -> ijl', sig_product_inv, mu_product)
    exponent = -np.sum(mu_product * np.sum(sig_product_inv.transpose(2,0,1,3)*mu_product, axis=3).transpose(1,2,0), axis=2) / 2
    norm = 1/( 2*np.pi*np.sqrt(sig_product_det) )
    cij = norm*np.exp(exponent)

    # 6) Dot product with weights
    ci = np.sum(cij*params[:,5], axis=1)

    return ci
def inverse2x2(matrix):
    # Instead of np.linalg - This is so much faster!!!
    det = matrix[...,0,0]*matrix[...,1,1] - matrix[...,0,1]*matrix[...,1,0]
    #inv = matrix.copy()
    #inv[...,0,0] = matrix[...,1,1]
    #inv[...,1,1] = matrix[...,0,0]
    #inv[...,[0,1],[1,0]] *= -1
    #inv *= 1/np.repeat(np.repeat(det[...,np.newaxis,np.newaxis], 2, axis=-1), 2, axis=-2)
    inv = np.array([[matrix[...,1,1]/det, -matrix[...,0,1]/det],
                    [-matrix[...,1,0]/det, matrix[...,0,0]/det]]).transpose(2,3,0,1)

    return inv, det
def gmm_product(mu1, mu2, sig1, sig2):

    sig1_i = inverse2x2(sig1)[0]
    sig2_i = inverse2x2(sig2)[0]
    sig3 = inverse2x2(sig1_i + sig2_i)[0]

    mu1 = np.repeat(mu1, mu2.shape[0], axis=0)[...,np.newaxis]
    mu2 = np.repeat(mu2, mu1.shape[1], axis=1)[...,np.newaxis]
    mu1 = np.repeat(mu1, 2, axis=3)
    mu2 = np.repeat(mu2, 2, axis=3)

    #mu3 = np.einsum('nmij, nmj -> nmi', np.matmul(sig3, sig1_i), mu1) + \
        #        np.einsum('nmij, nmj -> nmi', np.matmul(sig3, sig2_i), mu2)

    mu3 = np.matmul(np.matmul(sig3, sig1_i), mu1, out=np.zeros(sig3.shape)) + \
        np.matmul(np.matmul(sig3, sig2_i), mu2, out=np.zeros(sig3.shape))
    mu3 = mu3[...,0]

    return mu3, sig3
def gmm_product_p(params1, params2):

    sig1 = np.array([np.diag(a) for a in params1[:,2:4]])
    R = rotation(params1[:,4])
    sig1 = np.matmul(R, np.matmul(sig1, R.transpose(0,2,1)))

    sig2 = np.array([np.diag(a) for a in params2[:,2:4]])
    R = rotation(params2[:,4])
    sig2 = np.matmul(R, np.matmul(sig2, R.transpose(0,2,1)))

    mu1 = params1[:,:2]
    mu2 = params2[:,:2]

    sig1 = sig1[np.newaxis, ...]
    sig2 = sig2[:, np.newaxis, ...]
    mu1 = mu1[np.newaxis, ...]
    mu2 = mu2[:, np.newaxis, ...]
    mu3, sig3 = gmm_product(mu1, mu2, sig1, sig2)

    sig_norm_i, sig_norm_det = inverse2x2(sig1+sig2)
    mu_norm = mu1-mu2
    exponent = -np.sum(mu_norm * np.sum(sig_norm_i.transpose(2,0,1,3)*mu_norm, axis=3).transpose(1,2,0), axis=2) / 2
    norm = 1/( 2*np.pi*np.sqrt(sig_norm_det) )
    cij = norm*np.exp(exponent)

    w1 = params1[:,[5]][np.newaxis, ...]
    w2 = params2[:,[5]][:, np.newaxis, ...]
    cij = cij[..., np.newaxis]
    w3 = w1*w2*cij

    eigvals, eigvecs = np.linalg.eig(sig3)
    th3 = np.arctan2(eigvecs[...,0,1], eigvecs[...,0,0])[...,np.newaxis]

    params3 = np.concatenate((mu3, eigvals, th3, w3), axis=2)

    return params3

def feature_scaling(x, y, mux, muy, sx, sy):

    '''
    feature_scaling - Scales features to a zero mean and unit standard deviation

    Parameters
    ----------
        x, y - arr of float
            - x and y coordinates of points
        mux, muy - float
            - Mean of distribution in x and y coordinates
        sx, sy - floats
            - Standard deviation of coordinates in x and y coordinates
    Returns
    -------
        scalex, scaley - arr of float
            - x and y coordinates scaled by feature scaling

    '''

    scalex = (x-mux)/sx
    scaley = (y-muy)/sy

    return scalex, scaley

def covariance_scaling(sigxy, sx, sy):

    scaling = np.outer(np.array([sx, sy]), np.array([sx, sy]))
    return sigxy/scaling

def rotation(th):

    R = np.array([[np.cos(th), np.sin(th)],
                  [-np.sin(th), np.cos(th)]])

    return R.transpose(2,0,1)

"""
Optimizers
"""
def scipyOpt(function, params):

    bounds = None

    # result is the set of theta parameters which optimize the likelihood given x, y, yerr
    result = op.minimize(function, params.ravel(), method='Powell', bounds=bounds)
    params = result["x"]

    return params, result

def scipyAnneal(function, params):

    # result is the set of theta parameters which optimize the likelihood given x, y, yerr
    result = op.anneal(function, params.ravel())
    params = result["x"]

    return params, result

def scipyStoch(function, params):

    # result is the set of theta parameters which optimize the likelihood given x, y, yerr
    result = op.basinhopping(function, params.ravel(), niter=1)
    params = result["x"]

    return params, result

def emcee_opt(function, params, niter=2000, file_loc=''):

    pshape =params.shape
    foo = lambda pars: -function(pars.reshape(pshape))

    nwalkers=int(params.shape[0]*2.5)
    ndim=len(params.flatten())

    p0 = np.array([initParams(params.shape[0], [-20,20],[-20,20],
                    False,nstars=2000,runscaling=True,l_rng=[0.1, 1.])[0].flatten() for i in range(nwalkers)])

    sampler = emcee.EnsembleSampler(nwalkers, ndim, foo)
    # Run emcee
    _=sampler.run_mcmc(p0, niter)

    sampler.chain

    # Retrieve results
    nburn = niter/2
    burnt_values = sampler.chain[:,nburn:,:]
    burnt_values = burnt_values.reshape(-1, burnt_values.shape[-1])

    median = np.median(burnt_values, axis=0)

    lp = sampler.lnprobability
    index = np.unravel_index(np.argmax(lp), lp.shape)
    median = sampler.chain[index[0], index[1], :]

    if savefigs != '':
        import corner

        plt.figure( figsize=(10*params.shape[0], 60) )
        axes = plt.subplots(params.shape[1], params.shape[0])
        for i in xrange(median.shape[0]):
            for j in xrange(median.shape[1]):
                plt.sca(axes[i,j])
                for k in range(nwalkers):
                    plt.plot(np.arange(sampler.chain.shape[1]), sampler.chain[k,:,i], color="0.2", linewidth=0.1)
                burnt = sampler.chain[...,i].flatten()
                mean = np.mean(burnt)
                median = np.median(burnt)
                plt.plot([0,sampler.chain.shape[1]], [mean, mean], label='mean after burn in')
                plt.plot([0,sampler.chain.shape[1]], [median, median], label='median after burn in')
                plt.legend()
                plt.title("Dimension {0:d}".format(i))
                plt.savefig(file_loc, bbox_inches='tight')

        plt.figure( figsize=(20, 20) )
        fig = corner.corner(burnt_values, quantiles=[0.5], show_titles=True)
        plt.savefig(file_loc, bbox_inches='tight')


    return median, sampler

def emcee_ball(function, params, params_l=None, params_u=None, niter=2000):
    print 'emcee with %d iterations...' % niter

    pshape =params.shape
    foo = lambda pars: -function(pars.reshape(pshape))

    ndim=len(params.flatten())
    nwalkers=ndim*2

    p0 = np.repeat([params,], nwalkers, axis=0)
    p0 = np.random.normal(loc=p0, scale=np.abs(p0/500))
    p0[0,:] = params

    # Reflect out of bounds parameters back into the prior boundaries
    # Lower bound
    pl = np.repeat([params_l,], nwalkers, axis=0)
    lb = p0 < pl
    p0[lb] = pl[lb] + pl[lb] - p0[lb]
    # Upper bound
    pu = np.repeat([params_u,], nwalkers, axis=0)
    ub = p0 > pu
    p0[ub] = pu[ub] + pu[ub] - p0[ub]
    # Order eigenvalues
    p0[:,:,2:4] = np.sort(p0[:,:,2:4], axis=2)
    p0[:,:,0] = np.sort(p0[:,:,0], axis=1)
    #sort_i = p0[:,:,0].argsort(axis=1)
    #sort_j = np.repeat([np.arange(p0.shape[0]),], p0.shape[1], axis=0).T
    #p0 = p0[sort_j, sort_i, :]

    p0 = p0.reshape(nwalkers, -1)
    sampler = emcee.EnsembleSampler(nwalkers, ndim, foo)
    # Run emcee
    _=sampler.run_mcmc(p0, niter)

    # Retrieve results
    nburn = niter/2
    burnt_values = sampler.chain[:,nburn:,:]
    burnt_values = burnt_values.reshape(-1, burnt_values.shape[-1])

    median = np.median(burnt_values, axis=0)

    lp = sampler.lnprobability
    index = np.unravel_index(np.argmax(lp), lp.shape)
    median = sampler.chain[index[0], index[1], :]

    return median, sampler

def kmeans(sample, nComponents, n_iter=10, max_iter=100, weights=None, ndf=None):

    weighted_kde = not weights is None
    if weights is None: weights=np.ones(len(sample))

    params = np.zeros((nComponents, 6))
    from sklearn.cluster import KMeans

    kmc = KMeans(nComponents, n_init=n_iter, max_iter=max_iter)
    kmc.fit(sample, sample_weight=weights)

    means = kmc.cluster_centers_

    s0 = sample[kmc.labels_==0]
    for i in xrange(nComponents):
        sample_i = sample[kmc.labels_==i]
        delta = sample_i - means[i]
        sigma = np.matmul(delta.T, delta)/delta.shape[0]

        eigvals, eigvecs = np.linalg.eig(sigma)
        eigvecs = eigvecs[np.argsort(eigvals),:]
        eigvals = np.sort(eigvals)
        theta = np.arctan2(eigvecs[0,1], eigvecs[0,0])
        if theta<0: theta+=np.pi

        if not weighted_kde: w = sample_i.shape[0]
        else:
            w = np.sum(weights[kmc.labels_==i])/np.sum(weights)
            w *= float(sample.shape[0])/ndf

        params[i,:] = np.array([means[i,0], means[i,1], eigvals[0], eigvals[1], theta, w])

    return params

"""
INTEGRATION ROUTINES
"""
def integrationRoutine(function, param_set, nComponents, rngx, rngy, x_2d, y_2d, integration = "trapezium"):

    '''
    integrationRoutine - Chose the method by which the integrate the distribution over the specified region
                        of space then perform the integral.

    Parameters
    ----------
        function - function or interpolant
            - The function to be integrated over the specified region of space

        param_set - list of floats
            - Set of parameters which define the GMM.

        nComponents - int
            - Number of components of the GMM.

        rngx, rngy - tuple of floats
            - Boundary of region of colour-magnitude space being calculated.

    **kwargs
    --------
        integration='trapezium' - str
            - The type of integration routine to be tested
            - 'analytic', 'trapezium', 'simpson', 'cubature'

    Returns
    -------
        contInteg - float
            - Value of the integral after calculation
    '''

    # analytic if we have analytic solution to the distribution - this is the fastest
    if integration == "analytic": contInteg = multiIntegral(param_set, nComponents)
    # trapezium is a simple approximation for the integral - fast - ~1% accurate
    elif integration == "trapezium": contInteg = numericalIntegrate_precompute(function, x_2d, y_2d)
    elif integration == "analyticApprox": contInteg = bivGauss_analytical_approx(param_set, rngx, rngy)
    # simpson is a quadratic approximation to the integral - reasonably fast - ~1% accurate
    elif integration == "simpson": contInteg = simpsonIntegrate(function, *(rngx, rngy))
    # cubature is another possibility but this is far slower!
    elif integration == "cubature":
        contInteg, err = cubature(func2d, 2, 1, (rngx[0], rngy[0]), (rngx[1], rngy[1]))
        contInteg = float(contInteg)
    else: raise ValueError('No integration routine "%s"' % integration)

    return contInteg

def multiIntegral(params, nComponents):

    '''
    multiIntegral - Analytic integral over the specified bivariate Gaussian.

    Parameters
    ----------
        params - list of floats
            - Values of parameters for the Gaussian.

        nComponents - int
            - Number of components of the Gaussian Mixture Model

    Returns
    -------
        integral - float
            - Integral over the Gaussian Mixture Model
    '''

    integral = 0
    for i in range(nComponents):
        integral += bivariateIntegral(params[i])
    return integral

def bivariateIntegral(params):

    '''
    bivariateIntegral - Analytic integral over the specified bivariate Gaussian.

    Parameters
    ----------
        params - list of floats
            - Values of parameters for the Gaussian.

    Returns
    -------
        contInteg - float
            - Integral over the bivariate GAussian
    '''

    mux, sigmax, muy, sigmay, A, rho = params
    # Continuous integral of Bivariate Gaussian with infinite boundaries.
    contInteg = 2*np.pi * A * np.abs(sigmax * sigmay) * np.sqrt(1-rho**2)
    return contInteg

def numericalIntegrate(function, rngx, rngy, Nx_int=250, Ny_int=250):

    '''
    numericalIntegrate - Integrate over region using the trapezium rule

    Parameters
    ----------
        function - function or interpolant
            - The function to be integrated over the specified region of space

        nComponents - int
            - Number of components of the GMM.

        rngx, rngy - tuple of floats
            - Boundary of region of colour-magnitude space being calculated.


    **kwargs
    --------
        Nx_int, Ny_int: int
            - Number of grid spacings to place along the x and y axes

    Returns
    -------
        integral: float
            - Integral over the region
    '''

    #compInteg = integrate.dblquad(function, rngx[0], rngx[1], rngy[0], rngy[1])
    Nx_int, Ny_int = (Nx_int, Ny_int)

    x_coords = np.linspace(rngx[0], rngx[1], Nx_int)
    y_coords = np.linspace(rngy[0], rngy[1], Ny_int)

    dx = ( rngx[1]-rngx[0] )/Nx_int
    dy = ( rngy[1]-rngy[0] )/Ny_int

    x_2d = np.tile(x_coords, ( len(y_coords), 1 ))
    y_2d = np.tile(y_coords, ( len(x_coords), 1 )).T
    z_2d = function(*(x_2d, y_2d))

    volume1 = ( (z_2d[:-1, :-1] + z_2d[1:, 1:])/2 ) * dx * dy
    volume2 = ( (z_2d[:-1, 1:] + z_2d[1:, :-1])/2 ) * dx * dy
    integral = ( np.sum(volume1.flatten()) + np.sum(volume2.flatten()) ) /2

    return integral

def numericalIntegrate_mesh(function, rngx, rngy, Nx_int=250, Ny_int=250):

    '''
    numericalIntegrate - Integrate over region using the trapezium rule

    Parameters
    ----------
        function - function or interpolant
            - The function to be integrated over the specified region of space

        nComponents - int
            - Number of components of the GMM.

        rngx, rngy - tuple of floats
            - Boundary of region of colour-magnitude space being calculated.


    **kwargs
    --------
        Nx_int, Ny_int: int
            - Number of grid spacings to place along the x and y axes

    Returns
    -------
        integral: float
            - Integral over the region
    '''

    #compInteg = integrate.dblquad(function, rngx[0], rngx[1], rngy[0], rngy[1])
    Nx_int, Ny_int = (Nx_int, Ny_int)

    x_coords = np.linspace(rngx[0], rngx[1], Nx_int)
    y_coords = np.linspace(rngy[0], rngy[1], Ny_int)

    dx = ( rngx[1]-rngx[0] )/Nx_int
    dy = ( rngy[1]-rngy[0] )/Ny_int

    x_2d, y_2d = np.meshgrid(x_coords, y_coords)
    z_2d = function(*(x_2d, y_2d))

    volume1 = ( (z_2d[:-1, :-1] + z_2d[1:, 1:])/2 ) * dx * dy
    volume2 = ( (z_2d[:-1, 1:] + z_2d[1:, :-1])/2 ) * dx * dy
    integral = ( np.sum(volume1.flatten()) + np.sum(volume2.flatten()) ) /2

    return integral

def numericalIntegrate_precompute(function, x_2d, y_2d):

    z_2d = function(*(x_2d, y_2d))

    dx = x_2d[1:, 1:] - x_2d[1:, :-1]
    dy = y_2d[1:, 1:] - y_2d[:-1, 1:]

    volume1 = ( (z_2d[:-1, :-1] + z_2d[1:, 1:])/2 ) * dx * dy
    volume2 = ( (z_2d[:-1, 1:] + z_2d[1:, :-1])/2 ) * dx * dy
    integral = ( np.sum(volume1.flatten()) + np.sum(volume2.flatten()) ) /2

    return integral

def bivGauss_analytical_approx2(params, rngx, rngy):

    dl1 = find_dls(rngx, rngy, params)
    dl2 = find_dls(rngx, rngy, params, rotate=np.pi/2)

    # Assuming indices are xmin, ymin, xmax, ymax
    dl_asc1 = np.sort(dl1,axis=1)
    dl_asc2 = np.sort(dl2,axis=1)
    dlf1 = np.zeros((params.shape[0], 2))
    dlf2 = np.zeros((params.shape[0], 2))

    boundary_index_1a = np.argmin(np.abs(dl1), axis=1)
    boundary_index_1b = boundary_index_1a - 2 # To get opposite boundary
    boundary_index_2a = np.argmin(np.abs(dl2), axis=1)
    boundary_index_2b = boundary_index_2a - 2 # To get opposite boundary

    ncross1 = np.sum(dl1>0, axis=1)
    ncross2 = np.sum(dl2>0, axis=1)
    # Condition1 - Ellipse lies within boundaries
    con = (ncross1==2)&(ncross2==2)
    dlf1[con] = np.array([np.abs(dl1[con][np.arange(dlf1[con].shape[0]),boundary_index_1a[con]-1]),
                          np.abs(dl1[con][np.arange(dlf1[con].shape[0]),boundary_index_1b[con]-1])]).T
    dlf2[con] = np.array([np.abs(dl2[con][np.arange(dlf2[con].shape[0]),boundary_index_2a[con]-1]),
                          np.abs(dl2[con][np.arange(dlf2[con].shape[0]),boundary_index_2b[con]-1])]).T
    con = (ncross1==3)
    dlf1[con] = np.array([np.zeros(np.sum(con)), np.abs(dl_asc1[con][:,3])]).T
    con = (ncross2==3)
    dlf2[con] = np.array([np.zeros(np.sum(con)), np.abs(dl_asc2[con][:,3])]).T
    con = (ncross1==1)
    dlf1[con] = np.array([np.zeros(np.sum(con)), np.abs(dl_asc1[con][:,0])]).T
    con = (ncross2==1)
    dlf2[con] = np.array([np.zeros(np.sum(con)), np.abs(dl_asc2[con][:,0])]).T
    con = (ncross1==2)&((ncross2==0)|(ncross2==4))
    dlf1[con] = np.array([np.abs(dl_asc1[con][:,0]), np.abs(dl_asc1[con][:,3])]).T
    dlf2[con] = np.array([np.abs(dl2[con][np.arange(dlf2[con].shape[0]),boundary_index_2a[con]]),
                          np.abs(dl2[con][np.arange(dlf2[con].shape[0]),boundary_index_2b[con]])]).T
    con = ((ncross1==0)|(ncross1==4))&(ncross2==2)
    dlf1[con] = np.array([np.abs(dl1[con][np.arange(dlf1[con].shape[0]),boundary_index_1a[con]]),
                          np.abs(dl1[con][np.arange(dlf1[con].shape[0]),boundary_index_1b[con]])]).T
    dlf2[con] = np.array([np.abs(dl_asc2[con][:,0]), np.abs(dl_asc2[con][:,3])]).T

    dl = np.stack((dlf1, dlf2))

    erfs = spec.erf( dl / (np.sqrt(2) * np.repeat([params[:,2:4],], 2, axis=0)) ).transpose(1,2,0) / 2

    #comp_integral = np.zeros(erfs.shape[:2])
    #comp_integral[erfs[:,0,:]<0] = 0.5-erfs[:,1,:][erfs[:,0,:]<0]
    #comp_integral[erfs[:,0,:]>0] = np.sum(erfs, axis=1)[erfs[:,0,:]>0]
    comp_integral = np.sum(erfs, axis=1)
    comp_integral = np.prod(comp_integral, axis=1)
    integral = np.sum(comp_integral*params[:,5])

    return integral

def find_dls(rngx, rngy, params, rotate=0.):

    # shape 2,2 - xy, minmax
    rngxy = np.array([rngx, rngy])
    # shape n,2,1 - components, xy, minmax
    angle = np.array([np.sin(params[:,4]+rotate), np.cos(params[:,4]+rotate)]).T[:,:,np.newaxis]
    # shape n,2,1 - components, xy, minmax
    mean = params[:,:2][:,:,np.newaxis]

    # shape n,2,2 - components, xy, minmax
    dl = (rngxy - mean)/angle
    # at this point I know which boundaries belong to which coordinates
    # Can I chose which boundary indices to use here then index them lower down keeping the con options???
    # Get argmin of abs values, take index of argmin+2 as second boundary
    # shape n, 4 - components, xyminmax
    dl = dl.transpose(0,2,1).reshape(dl.shape[0], -1)

    """
    #NOT sure if this works
    boundary_index_1 = np.argmin(np.abs(dl), axis=1)
    boundary_index_2 = boundary_index_1 - 2 # To get opposite boundary
    # Get rid of dl.sort
    dltest = dl.copy()
    # Assuming indices are xmin, ymin, xmax, ymax

    dl.sort(axis=1)

    dlf = np.zeros((params.shape[0], 2))
    con = np.sum(dl>0, axis=1)==4
    dlf[con] = np.array([np.zeros(np.sum(con))-1, dl[con][:,0]]).T
    con = np.sum(dl>0, axis=1)==3
    dlf[con] = np.array([np.zeros(np.sum(con))-1, dl[con][:,1]]).T
    con = np.sum(dl>0, axis=1)==2
    dlf[con] = np.array([-dl[con][:,1],  dl[con][:,2]]).T
    dlf[con] = np.array([np.abs(dltest[con][np.arange(dlf[con].shape[0]),boundary_index_1[con]]),
                        np.abs(dltest[con][np.arange(dlf[con].shape[0]),boundary_index_2[con]])]).T
    con = np.sum(dl>0, axis=1)==1
    dlf[con] = np.array([np.zeros(np.sum(con))-1, -dl[con][:,2]]).T
    con = np.sum(dl>0, axis=1)==0
    dlf[con] = np.array([np.zeros(np.sum(con))-1, -dl[con][:,3]]).T"""

    return dl

def bivGauss_analytical_approx(params, rngx, rngy):

    # shape 2,4 - xy, corners
    corners = np.array(np.meshgrid(rngx, rngy)).reshape(2, 4)
    # shape n,2,1 - components, xy, corners
    angle1 = np.array([np.cos(params[:,4]), -np.sin(params[:,4])]).T[:,:,np.newaxis]
    angle2 = np.array([np.sin(params[:,4]), np.cos(params[:,4])]).T[:,:,np.newaxis]
    # shape n,2,1 - components, xy, minmax
    mean = params[:,:2][:,:,np.newaxis]

    #print 'Delta', corners-mean

    #print 'Corners: ', corners
    # shape n,4 - components, corners
    dl1 = np.sum( (corners - mean)*angle1 , axis=1)
    dl2 = np.sum( (corners - mean)*angle2 , axis=1)
    # shape 2,n,4 - axes, components, corners
    dl = np.stack((dl1, dl2))
    #print 'Dl: ', dl[:,0,:]
    dl.sort(axis=2)
    # shape 2,n,2 - axes, components, extreme corners
    dl = dl[..., [0,-1]]
    #print 'Dl minmax: ', dl

    # shape 2,n,2 - axes, components, extreme corners
    component_vars = np.repeat([params[:,2:4],], 2, axis=0).transpose(2,1,0)
    #print 'vars: ', component_vars
    # Use erfc on absolute values to avoid high value precision errors
    erfs = spec.erfc( dl / (np.sqrt(2)*np.sqrt(component_vars) ) )
    #print 'erfs: ', erfs
    #print 'ratio: ', dl / (np.sqrt(2) * component_stds)
    #print 'erfs: ', erfs
    #print 'sigmas: ', np.repeat([params[:,2:4],], 2, axis=0).transpose(2,1,0)
    #print 'erfs: ', erfs[:,1,:]

    # Sum integral lower and upper bounds
    comp_integral = np.abs(erfs[...,1]-erfs[...,0]) / 2
    #print 'comp: ', comp_integral[:,1]
    # Product the axes of the integrals
    comp_integral = np.prod(comp_integral, axis=0)
    #return comp_integral
    # Sum weighted Gaussian components
    integral = np.sum(comp_integral*params[:,5])

    return integral

def simpsonIntegrate(function, rngx, rngy):

    '''
    simpsonIntegrate - Integrate over region using simson's rule

    Parameters
    ----------
        function - function or interpolant
            - The function to be integrated over the specified region of space

        nComponents - int
            - Number of components of the GMM.

    Returns
    -------
        integral: float
            - Integral over the region
    '''

    #compInteg = integrate.dblquad(function, rngx[0], rngx[1], rngy[0], rngy[1])
    Nx_int, Ny_int = 100, 250

    x_coords = np.linspace(rngx[0], rngx[1], Nx_int)
    y_coords = np.linspace(rngy[0], rngy[1], Ny_int)

    dx = ( rngx[1]-rngx[0] )/Nx_int
    dy = ( rngy[1]-rngy[0] )/Ny_int

    x_2d = np.tile(x_coords, ( len(y_coords), 1 ))
    y_2d = np.tile(y_coords, ( len(x_coords), 1 )).T
    z_2d = function(*(x_2d, y_2d))

    z_intx = function(*(x_2d + dx/2, y_2d))[:-1, :]
    z_inty = function(*(x_2d, y_2d + dy/2))[:,:-1]

    volume1 = ( (z_2d[:-1, :] + z_intx*4 + z_2d[1:, :] ) /6 ) * dx * dy
    volume2 = ( (z_2d[:, :-1] + z_inty*4 + z_2d[:, 1:] ) /6 ) * dx * dy
    integral = ( np.sum(volume1.flatten()) + np.sum(volume2.flatten()) ) /2

    return integral

def gridIntegrate(function, rngx, rngy):

    '''
    gridIntegrate - Integrate over the grid when using PenalisedGridModel

    Parameters
    ----------
        function - function or interpolant
            - The function to be integrated over the specified region of space

        nComponents - int
            - Number of components of the GMM.

    Returns
    -------
        compInteg: float
            - Integral over the region
    '''

    #compInteg = integ.dblquad(function, rngx[0], rngx[1], rngy[0], rngy[1])
    compInteg, err = cubature(function, 2, 1, (rngx[0], rngy[0]), (rngx[1], rngy[1]))

    return compInteg

"""
TESTS
"""
def singleGaussianSample(mu, sigma, N = 1000):

    # Generate 2D sample with mean=0, std=1
    sample = np.random.normal(size=(2, N))

    # Convert sample to given mean and covariance
    A = np.linalg.cholesky(sigma)
    sample = mu + np.matmul(A, sample).T

    return sample



"""
BayesianGaussianMixture and TNC methods
"""
class BGM_TNC():

    '''
    GaussianEM - Class for calculating bivariate Gaussian mixture model which best fits
                 the given poisson point process data.

    Parameters
    ----------
        x, y - np.array of floats
            - x and y coordinates for the points generated via poisson point process from
            the smooth model

        nComponents - int
            - Number of Gaussian components of the mixture model

        rngx, rngy - tuple of floats
            - Upper and lower bounds on x and y of the survey

    Functions
    ---------
        __call__ - Returns the value of the smooth GMM distribution at the points x, y
        optimizeParams - Vary the parameters of the distribution using the given method to optimize the
                        poisson likelihood of the distribution
        optimize
        lnprob - ln of the posterior probability of the distribution given the parameters.
               - posterior probability function is proportional to the prior times the likelihood
               - lnpost = lnprior + lnlike
        lnprior - The test of the parameters of the Gaussian Mixture Model against the specified prior values
    '''

    def __init__(self, x=np.zeros(0), y=np.zeros(0), sig_xy=None,
                rngx=(0,1), rngy=(0,1), runscaling=True, runningL=True,
                photoDF=None, priorDF=False):

        # Iteration number to update
        self.iter_count = 0

        # Name of the model to used for reloading from dictionary
        self.modelname = self.__class__.__name__

        # Distribution from photometric survey for calculation of SF
        self.photoDF = photoDF
        self.priorDF = priorDF

        # Starting values for parameters
        self.params_i = None
        # Final optimal values for parameters
        self.params_f = None
        # Shape of parameter set (number of components x parameters per component)
        self.param_shape = ()

        # Boundary on minimum std
        self.s_min=s_min

        # Coordinate covariance matrix
        if sig_xy is None:
            z_ = np.zeros(len(x))
            sig_xy = np.array([[z_, z_],[z_, z_]]).transpose(2,0,1)
        self.sig_xy = sig_xy

        self.runscaling = runscaling
        # Not run when loading class from dictionary
        if runscaling:
            # Real space parameters
            self.x = x.copy()
            self.y = y.copy()
            self.rngx, self.rngy = rngx, rngy
            # Statistics for feature scaling
            if len(x)>1:
                self.mux, self.sx = np.mean(x), np.std(x)
                self.muy, self.sy = np.mean(y), np.std(y)
            else:
                # SD=0 if only one point which causes problems!
                self.mux, self.sx = np.mean(x), (rngx[1]-rngx[0])/4
                self.muy, self.sy = np.mean(y), (rngy[1]-rngy[0])/4
            # Scaled parameters
            self.x_s, self.y_s = feature_scaling(x, y, self.mux, self.muy, self.sx, self.sy)
            self.rngx_s, self.rngy_s = feature_scaling(np.array(rngx), np.array(rngy), self.mux, self.muy, self.sx, self.sy)
            self.sig_xy_s = covariance_scaling(self.sig_xy, self.sx, self.sy)
        else:
            # Real space parameters
            self.x = x.copy()
            self.y = y.copy()
            self.rngx, self.rngy = rngx, rngy
            self.x_s, self.y_s = x, y
            self.rngx_s, self.rngy_s = rngx, rngy
            self.sig_xy_s = sig_xy

        # Function which calculates the actual distribution
        self.distribution = bivGaussMix_vect

        # Print out likelihood values as calculated
        self.runningL = runningL

        if self.priorDF:
            # Calculate Gaussian distributions from product of scaled DF and scaled star positions
            if self.runscaling: self.params_df = self.scaleParams(self.photoDF.params_f, dfparams=True)
            else: self.params_df = self.photoDF.params_f
            function = lambda a, b: self.distribution(self.params_df, a, b)
            #if self.runningL:
            #    print 'DF integral = ', numericalIntegrate_precompute(function, self.x_2d, self.y_2d)
            self.ndf = len(self.photoDF.x)
        else: self.ndf = None

    def __call__(self, x, y, components=None, params=None):

        '''
        __call__ - Returns the value of the smooth GMM distribution at the points x, y

        Parameters
        ----------
            x, y - float or np.array of floats
                - x and y coordinates of points at which to take the value of the GMM
                - From input - x is magnitude, y is colour

            components=None:
                - List of components to check for distribution values

            params=None:
                - The parameters on which the model will be evaluatedself.
                - If None, params_f class attribute will be used

        Returns
        -------
            GMMval: float or np.array of floats
                - The value of the GMM at coordinates x, y
        '''
        #
        if params is None: params=self.params_f.copy()

        # Scale x and y to correct region - Currently done to params_f - line 371  - but could change here instead
        #x, y = feature_scaling(x, y, self.mux, self.muy, self.sx, self.sy)
        #rngx, rngy = feature_scaling(np.array(self.rngx), np.array(self.rngy), self.mux, self.muy, self.sx, self.sy)
        rngx, rngy = np.array(self.rngx), np.array(self.rngy)

        # Value of coordinates x, y in the Gaussian mixture model
        if components is None: components = np.arange(self.nComponents)
        GMMval = self.distribution(params[components, :], x, y)

        if (type(GMMval) == np.array)|(type(GMMval) == np.ndarray)|(type(GMMval) == pd.Series):
            # Not-nan input values
            notnan = (~np.isnan(x))&(~np.isnan(y))
            # Any values outside range - 0
            constraint = (x[notnan]>=rngx[0])&(x[notnan]<=rngx[1])&(y[notnan]>=rngy[0])&(y[notnan]<=rngy[1])
            GMMval[~notnan] = np.nan
            GMMval[notnan][~constraint] = 0.
        elif (type(GMMval) == float) | (type(GMMval) == np.float64):
            if (np.isnan(x))|(np.isnan(y)): GMMval = np.nan
            else:
                constraint = (x>=rngx[0])&(x<=rngx[1])&(y>=rngy[0])&(y<=rngy[1])
                if not constraint:
                    GMMval = 0.
        else: raise TypeError('The type of the input variables is '+str(type(GMMval)))

        return GMMval

    def optimizeParams(self):

        '''
        optimizeParams - Initialise and optimize parameters of Gaussian mixture model.

        **kwargs
        --------

        Returns
        -------

        '''

        if not self.priorDF:
            params = optimize(None, 'BGM')
        if self.priorDF:
            # Generate NIW prior parameters
            priorParams = NIW_prior_params(Xobs)
            # Run optimize
            params = optimize(priorParams, 'TNC')

        return params

    def optimize(self, params, method):

        '''
        optimize - Run scipy.optimize.minimize to determine the optimal parameters for the distribution

        Parameters
        ----------
            params: array of float
                - Initial parameters of the distribution

            method: str
                - Method to be used by optimizer, e.g. "Powell"

            bounds: list of tup of float or None
                - Boundaries of optimizing region
                - Only for optimization methods which take bounds

        Returns
        -------
            result: dict
                - Output of scipy.optimize
        '''

        X = np.vstack((self.x_s, self.y_s)).T

        # To clean up any warnings from optimize
        invalid = np.seterr()['invalid']
        divide = np.seterr()['divide']
        over = np.seterr()['over']
        np.seterr(invalid='ignore', divide='ignore', over='ignore')

        if method=='TNC':
            raw_params = TNC_sf(X, priorParams, self.params_df)
            params = transform_sfparams_logit(raw_params)
        elif method=='BGM':
            params = BGMM_df(X)

        params = params.reshape(self.param_shape)

        # To clean up any warnings from optimize
        np.seterr(invalid=invalid, divide=divide, over=over)
        if self.runningL: print("")

        return params

    def scaleParams(self, params_in, dfparams=False):

        # This isn't quite right, the likelihood doesn't turn out the same!

        params = params_in.copy()
        params[:,[0,1]] = (params[:,[0,1]] -  [self.mux, self.muy]) / np.array([self.sx, self.sy])

        params[:,[2,3]] *= 1/np.array([self.sx**2, self.sy**2])

        #if self.priorDF & (not dfparams):
        #    params[:,5] /= (self.sx*self.sy)

        return params

    def unscaleParams(self, params_in, dfparams=False):

        params = params_in.copy()
        params[:,[0,1]] = (params[:,[0,1]] * np.array([self.sx, self.sy])) +  [self.mux, self.muy]

        params[:,[2,3]] *= np.array([self.sx**2, self.sy**2])

        #if self.priorDF & (not dfparams):
        #    params[:,5] *= (self.sx*self.sy)

        return params

# General functions
def quick_invdet(S):
    det = S[:,0,0]*S[:,1,1] - S[:,0,1]**2
    Sinv = S.copy()*0
    Sinv[:,0,0] = S[:,1,1]
    Sinv[:,1,1] = S[:,0,0]
    Sinv[:,0,1] = -S[:,0,1]
    Sinv[:,1,0] = -S[:,1,0]
    Sinv *= 1/det[:,np.newaxis,np.newaxis]

    return Sinv, det
def Gaussian_i(delta, Sinv, Sdet):
    # delta is [nStar, nComponent, 2]
    # Sinv is [nComponent, 2, 2]
    # Sdet is [nComponent]

    sum_axis = len(delta.shape)-1
    exponent = -0.5*np.sum(delta * \
                    np.sum(Sinv[np.newaxis,...]*delta[...,np.newaxis], axis=sum_axis), axis=sum_axis)
    norm = 1/(2*np.pi*np.sqrt(Sdet))

    # Return shape is (Nstar, Ncomponent)
    return norm[np.newaxis] * np.exp(exponent)
def Gaussian_int(delta, Sinv, Sdet):
    # delta is [nComponent, 2]
    # Sinv is [nComponent, 2, 2]
    # Sdet is [nComponent]

    sum_axis = len(delta.shape)-1
    exponent = -0.5*np.sum(delta * \
                    np.sum(Sinv*delta[...,np.newaxis], axis=sum_axis), axis=sum_axis)
    norm = 1/(2*np.pi*np.sqrt(Sdet))

    # Return shape is (Ncomponent)
    return norm * np.exp(exponent)

# Manipulating parameters
def NIW_prior_params(Xsf):

    mu0 = np.mean(Xsf, axis=0)
    Psi0 = np.mean((Xsf-mu0)[...,np.newaxis] * (Xsf-mu0)[...,np.newaxis,:], axis=0)
    l0 = 10.
    nu0 = 1.
    priorParams = [mu0, l0, Psi0, nu0]

    return priorParams
def get_params(gmm_inst, Nstar, n_components):

    params = np.zeros((n_components,6))
    params[:,:2] = gmm_inst.means_
    params[:,2:4] = gmm_inst.covariances_[:,[0,1],[0,1]]
    params[:,4] = gmm_inst.covariances_[:,0,1]/np.sqrt(gmm_inst.covariances_[:,0,0]*gmm_inst.covariances_[:,1,1])
    params[:,5] = gmm_inst.weights_*Nstar

    return params
def get_sfparams_logit(gmm_inst, n_components):

    params = np.zeros((n_components,6))
    params[:,:2] = gmm_inst.means_
    params[:,2:4] = gmm_inst.covariances_[:,[0,1],[0,1]]
    corr = gmm_inst.covariances_[:,0,1]/np.sqrt(gmm_inst.covariances_[:,0,0]*gmm_inst.covariances_[:,1,1])
    params[:,4] = (corr+1)/2.
    params[:,4] = np.log(params[:,4]/(1-params[:,4]))

    w = gmm_inst.weights_
    Sdet = quick_invdet(gmm_inst.covariances_)[1]
    w /= (2*np.pi*np.sqrt(Sdet))
    w[w>1] = 0.9
    params[:,5] = np.log(w/(1-w))
    # Logit on correlation

    return params
def transform_sfparams_logit(params):

    raw_params = params.copy().reshape(-1,6)

    raw_params[:,2:4] = np.abs(raw_params[:,2:4])

    e_alpha = np.exp(-raw_params[...,4])
    p = 0.999/(1+e_alpha)
    corr = np.sqrt(raw_params[...,2]*raw_params[...,3])*(2*p - 1)
    S_sf = np.moveaxis(np.array([[raw_params[...,2], corr], [corr, raw_params[...,3]]]), -1, 0)
    Sinv_sf, Sdet_sf = quick_invdet(S_sf)
    raw_params[...,4] = (2*p - 1)

    # Logit correction of raw_params[:,5] - [-inf, inf] --> [0, rt(det(2.pi.S))]
    e_alpha_pi = np.exp(-raw_params[...,5])
    p_pi = 1./(1+e_alpha_pi)
    pi = 2*np.pi*np.sqrt(Sdet_sf) * p_pi
    raw_params[:,5] = pi

    return raw_params

# Likelihood, Prior and Posterior functions
def calc_nlnP_grad_pilogit_NIW(params, Xsf, NIWprior, df_params, stdout=False):

    # Parameters - transform to means, covariances and weights
    params = np.reshape(params, (-1,6))
    # means
    params[:,2:4] = np.abs(params[:,2:4])
    df_idx = np.repeat(np.arange(df_params.shape[0]), params.shape[0])
    sf_idx = np.tile(np.arange(params.shape[0]), df_params.shape[0])
    # covariances
    e_alpha = np.exp(-params[...,4])
    p = 0.999/(1+e_alpha)
    corr = np.sqrt(params[...,2]*params[...,3])*(2*p - 1)
    S_sf = np.moveaxis(np.array([[params[...,2], corr], [corr, params[...,3]]]), -1, 0)
    Sinv_sf, Sdet_sf = quick_invdet(S_sf)
    delta_sf = Xsf[:,np.newaxis,:]-params[:,:2][np.newaxis,:,:]
    Sinv_sf_delta = np.sum(Sinv_sf[np.newaxis,...]*delta_sf[...,np.newaxis], axis=2)
    #weights
    # Logit correction of params[:,5] - [-inf, inf] --> [0, rt(det(2.pi.S))]
    e_alpha_pi = np.exp(-params[...,5])
    p_pi = 1./(1+e_alpha_pi)
    pi = 2*np.pi*np.sqrt(Sdet_sf) * p_pi


    # Likelihood
    corr = np.sqrt(df_params[...,2]*df_params[...,3])*df_params[...,4]
    S_df = np.moveaxis(np.array([[df_params[...,2], corr], [corr, df_params[...,3]]]), -1, 0)

    Sinv_sum, Sdet_sum = quick_invdet(S_sf[sf_idx]+S_df[df_idx])
    Sinv_sum_delta = np.sum(Sinv_sum*(df_params[:,:2][df_idx] - params[:,:2][sf_idx])[...,np.newaxis], axis=1)

    # Star iteration term
    # (Nstar x Ncomponent_sf)
    m_ij = Gaussian_i(delta_sf, Sinv_sf, Sdet_sf)
    m_i = np.sum(pi*m_ij, axis=1)

    # Integral Term
    delta_mumu = params[:,:2][sf_idx] - df_params[:,:2][df_idx]
    I_jl = Gaussian_int(delta_mumu, Sinv_sum, Sdet_sum)  * pi[sf_idx] * df_params[:,5][df_idx]
    I = np.sum(I_jl)

    # Prior
    m0, l0, Psi0, nu0 = NIWprior
    delta_mumu0 = params[:,:2]-m0[np.newaxis,:]
    Prior0 = (-(nu0+4.)/2.) * np.log(Sdet_sf)
    Priormu = (-l0/2.) * np.sum(delta_mumu0 * np.sum(Sinv_sf * delta_mumu0[...,np.newaxis], axis=1), axis=1)
    PriorS = (-1/2.) * np.trace(np.matmul(Psi0[np.newaxis,...], Sinv_sf), axis1=-2, axis2=-1)
    Prior = np.sum(Prior0 + Priormu + PriorS)
    # Calculation for later
    Sinv_delta_mumu0 = np.sum(Sinv_sf * delta_mumu0[...,np.newaxis], axis=1)

    # Gradients

    # Pi
    A = np.sum(m_ij/m_i[:,np.newaxis], axis=0) # i-term
    B = np.sum((I_jl/pi[sf_idx]).reshape(df_params.shape[0], params.shape[0]), axis=0) # int-term
    C = 1/pi # prior-term
    gradPi = A - B

    # mu
    A = (pi[np.newaxis,:]*(m_ij/m_i[:,np.newaxis]))[...,np.newaxis]*\
        Sinv_sf_delta
    A = np.sum(A, axis=0) # i-term (nComponent x 2)
    B = (I_jl)[:,np.newaxis] * \
        Sinv_sum_delta
    B = np.sum(B.reshape(df_params.shape[0], params.shape[0], 2), axis=0) # int-term
    C = -l0 * np.sum(Sinv_sf * delta_mumu0[...,np.newaxis], axis=1) # NIW prior
    gradmu = A - B + C

    #sigma
    diff = -0.5*(Sinv_sf[np.newaxis] - Sinv_sf_delta[...,np.newaxis]*Sinv_sf_delta[...,np.newaxis,:])
    A = (pi*(m_ij/m_i[:,np.newaxis]))[...,np.newaxis,np.newaxis] * diff
    A = np.sum(A, axis=0) # i-term (nComponent x 2 x 2)
    diff = -0.5*(Sinv_sum - Sinv_sum_delta[...,np.newaxis]*Sinv_sum_delta[...,np.newaxis,:])
    B = (I_jl)[:,np.newaxis,np.newaxis] * diff
    B = np.sum(B.reshape(df_params.shape[0], params.shape[0], 2, 2), axis=0) # int-term (nComponent x 2 x 2)
    C = -((nu0+4)/2.)*Sinv_sf - (l0/2.)*Sinv_delta_mumu0[...,np.newaxis]*Sinv_delta_mumu0[...,np.newaxis,:]\
        + (1./2.) * np.matmul(Sinv_sf, np.matmul(Psi0[np.newaxis,...], Sinv_sf))
    gradS = A - B + C

    grad = np.zeros((params.shape[0],6))
    grad[:,:2] = gradmu
    grad[:,2] = gradS[:,0,0]
    grad[:,3] = gradS[:,1,1]
    grad[:,4] = 2*gradS[:,0,1]*np.sqrt(params[:,2]*params[:,3])*2*p**2*e_alpha
    grad[:,5] = gradPi * 2*np.pi*np.sqrt(Sdet_sf) * p_pi**2 * e_alpha_pi

    return  - ( np.sum(np.log(m_i)) - np.sum(I) + Prior), -grad.flatten()
def lnlike(Xdf, params):

    function = lambda a, b: sm.bivGaussMix_vect(params, a, b)
    model = sm.bivGaussMix_vect(params, Xdf[:,0], Xdf[:,1])

    contPoints = np.sum( np.log(model) )

    # Integral of the smooth function over the entire region
    contInteg = np.sum(params[:,5])
    #print bivGauss_analytical_approx(params, self.rngx_s, self.rngy_s), self.rngx_s, self.rngy_s

    lnL = contPoints - contInteg

    return lnL
def BIC(n, k, lnL):
    return k*np.log(n) - 2*lnL

# Optimization methods
def TNC_sf(Xsf, priorParams, df_params, max_components=15, stdout=False):

    bic_vals = np.zeros(max_components) + np.inf
    sf_params_n = {}
    for i in range(1, max_components):

        n_component=i

        # Simple GMM
        gmm = mixture.BayesianGaussianMixture(n_component, n_init=1,
                                              init_params='kmeans', tol=1e-5, max_iter=1000)
        gmm.fit(Xsf)
        params_bgm_pilogit = get_sfparams_logit(gmm, n_component)


        opt = scipy.optimize.minimize(calc_nlnP_grad_pilogit_NIW,  params_bgm_pilogit,
                                      args=(Xsf, priorParams, df_params), method='TNC',
                                      jac=True, options={'maxiter':500}, tol=1e-5)
        nlnp = calc_nlnP_grad_pilogit_NIW(opt.x, Xsf, priorParams, df_params)[0]

        bic_val = BIC(Xsf.shape[0], i*6, -nlnp)
        bic_vals[i] = bic_val
        if stdout:
            print(opt.success, opt.message)
            print(i, "...", bic_val, "...", nlnp)

        sf_params_n[i] = opt.x.reshape(-1,6)

        #if i>1:
        #    if (bic_vals[i]>bic_vals[i-1]) and (bic_vals[i-1]>bic_vals[i-2]):
        #        break

    if stdout:
        print('Best components: ', np.argmin(bic_vals))

    return sf_params_n[np.argmin(bic_vals)]
def BGMM_df(Xdf, max_components=20, stdout=False):

    max_components=20
    bic_vals = np.zeros(max_components+1) + np.inf
    df_params_n = {}

    for i in range(1, max_components+1):

        # Simple GMM
        gmm = mixture.BayesianGaussianMixture(n_components=i, n_init=2,
                                              init_params='kmeans', tol=1e-5, max_iter=1000)
        gmm.fit(Xdf)

        params = get_params(gmm, Xdf.shape[0], i)
        bic_val = BIC(Xdf.shape[0], i*6, lnlike(Xdf, params))
        bic_vals[i] = bic_val
        if stdout:
            print(i, "...", bic_val)

        df_params_n[i] = params

        if i>1:
            if (bic_vals[i]>bic_vals[i-1]) and (bic_vals[i-1]>bic_vals[i-2]):
                break

    if stdout:
        print('Best components: ', np.argmin(bic_vals))

    return df_params_n[np.argmin(bic_vals)]







# Used when Process == "Number"
class FlatRegion:

    '''
    FlatRegion - Model with constant value over entire region

    Parameters
    ----------
        value: float
            - value of selection function in region
        rangex, rangey: tuple of float
            - x and y ranges of region

    Returns
    -------
        result: float or arr of float
            - Value of selection function at x, y coordinates
    '''

    def __init__(self, value, rangex, rangey):

        # Name of the model to used for reloading from dictionary
        self.modelname = self.__class__.__name__

        self.value = value
        self.rangex = rangex
        self.rangey = rangey

    def __call__(self, x, y):

        '''
        __call__ - Calculate the selection function at given coordinates

        Parameters
        ----------
            x, y: arr of float
                - Coordinates at which we are calculating the selecion function

        Inherited
        ---------
            rangex, rangey: tuple of float
                - x and y ranges of region

        Returns
        -------
            result: float or arr of float
                - Value of selection function at x, y coordinates

        '''

        result = np.zeros(np.shape(x))
        result[(x>self.rangex[0]) & \
                (x<self.rangex[1]) & \
                (y>self.rangey[0]) & \
                (y<self.rangey[1])] = self.value

        return resul
