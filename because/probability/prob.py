# Probability Space Module provides all Probability Layer capabilities.

import numpy as np
import math
from math import log, sqrt, ceil
try:
    from because.probability import probCharts
except:
    pass
from because.probability.pdf import PDF
from because.probability import uprob
from because.probability.rkhs import rkhsMV
from because.probability.rcot.RCoT import RCoT
from sklearn.ensemble import RandomForestRegressor
from sklearn.ensemble import RandomForestClassifier
from because.probability import direction
from because.probability.standardiz import standardize
from because.probability.ucm import ucm

DEBUG = False

MAX_DISCRETE_VALS = 1000

class ProbSpace:
    def __init__(self, ds, categorical=[], density = 1.0, power=1, discSpecs = None, 
                cMethod = 'd!', textInfo=None):
        """ Probability Space (i.e. Joint Probability Distribution) based on a a multivariate dataset
            of random variables provided.  'JPS' (Joint Probability Space) is an alias for ProbSpace.
            The Joint Probability Space is a multi-dimensional probability distribution that embeds all
            knowledge about the statistical relationships among the variables, and supports a powerful
            range of queries to expose that information.
            It can handle discrete as well as continuous variables.  Continuous probabilities
            are managed by dense discretization (binning) continuous variables into small ranges.
            By default, the number of discretized bins for continuous variables is the square
            root of the number of samples.  This can be increased or decreased using the "density"
            parameter (see below).  Discrete variables may be binary, categorical or numeric(integer).

            Data(ds) is provided as a dictionary of variable name -> ValueList (List of variable values).
            ValueList should be the same length for all variables.  Each index in ValueList represents
            one sample.

            The density parameter is used to increase or decrease the number of bins used for continuous
            variables.  If density is 1 (default), then sqrt(N-samples) bins are used.  If density is
            set to D, then D * sqrt(N-samples) bins are used.

            The power parameter is used for stochastic approximation of conditional probabilities.
            Power may range from 0 (test conditionality at mean only) up to 100 (test every comination
            of discretized variables).  Values > 0 test more and more points for conditional dependence,
            until at 100, all points are tested.  For linear relationships, power of 0 or 1 is sufficient,
            while for complex, discontinuous relationships, higher values may be necessary to achieve
            high precision.  Power allows a tradeoff between precision and run-time.  High values of
            power may result in unacceptably long run-times.  In practice, power <= 8 should suffice in
            most cases.

            The discSpecs parameter is used to make recursive calls to the module while
            maintaining the discretization information, and should not be provided by the user.

            ProbSpace includes a 'Plot' function that requires matplotlib, and produces a probability
            distribution plot for each variable.

            cMethod determines the methods used for conditional probability of continuous variables.
            - 'u' default uses U-Prob, which automatically balances between D-Prob and J-Prob
            - 'd' forces D-Prob
            - 'j' forces J-Prob

            The main functions of ProbSpace are:
            - P(...) -- Returns the numerical probability of an event, given a set of conditions.
                P can return joint probabilities as well as univariate probabilities.
            - E(...) -- Returns the expected value (i.e. mean) of a variable, given a set of conditions.
            - distr(...) -- Returns a univariate probability distribution (see pdf.py) of a variable
                given a set of conditions.
            - dependence(...) -- Measures the dependence between two variables with optional conditioning
                on a set of other variables (i.e. conditional dependence.).
        """
        assert type(ds) == type({}), "Error -- Data must be in the form of a dictionary varName -> [val1, val2, ... , valN]"
        self.power = power
        self.ds = ds
        self.density = density
        self.fieldList = list(ds.keys())
        self.fieldTypes = []
        self.fieldIndex = {}
        self.stringMap = {}
        self.stringMapR = {}
        for i in range(len(self.fieldList)):
            key = self.fieldList[i]
            self.fieldIndex[key] = i
        if textInfo is not None:
            # For efficiency, this info gets passed from parent space
            # so we don't need to recompute.
            categoricalVars, fieldTypes, stringMap, stringMapR = textInfo
            self.categoricalVars = categoricalVars
            self.fieldTypes = fieldTypes
            self.stringMap = stringMap
            self.stringMapR = stringMapR
        else:
            # Stand alone space.  Compute all the basic info about the variables.
            self.categoricalVars = categorical
            for i in range(len(self.fieldList)):
                varName = self.fieldList[i]
                vals = self.ds[varName]
                if vals:
                    testVal = self.ds[self.fieldList[i]][0]
                    if type(testVal) == type(''):
                        self.fieldTypes.append('s')
                        self.stringMap[varName] = {}
                        self.stringMapR[varName] = {}
                        numVals = self.convertToNumeric(varName, vals)
                        self.ds[varName] = numVals
                        #if varName not in self.categoricalVars:
                            # All string vars are categorical.
                        #    self.categoricalVars.append(varName)
                    else:
                        self.fieldTypes.append('n')
                        # Verify that all values are numeric
                        for i in range(len(vals)):
                            val = vals[i]
                            try:
                                val = float(val)
                            except:
                                print('Warning: ProbSpace: bad value in variable', varName, '. Value = ', repr(val), \
                                    '.  Setting to zero.  This will likely affect data integrity.')
                                vals[i] = 0.0
        # Convert to Numpy array
        npDat = []
        for field in self.fieldList:
            npDat.append(self.ds[field])
        self.aData = np.array(npDat, 'float64')

        self.N = self.aData.shape[1]

        self.probCache = {} # Probability cache
        self.distrCache = {} # Distribution cache
        self.expCache = {} # Expectation cache
        self.rkhsCache = {} # RKHS Cache
        self.mlmodelCache = {}  # MLmodel Cache
        self.dependCache = {}  # Dependence Cache
        self.dirCache = {} # Direction Cache
        self.standCache = {} # Data standardization Cache

        self._discreteVars = self._getDiscreteVars()
        self.fieldAggs = self.getAgg()
        if self.N:
            if discSpecs:
                self.discSpecs = self.fixupDiscSpecs(discSpecs)
                #self.discretized = self.discretize()
            else:
                self.discSpecs = self.calcDiscSpecs()
                #self.discretized = self.discretize()
        self.cMethod = cMethod
        # For SubSpaces only
        # parentProb is the probability of the subspace within the parent space
        self.parentProb = None
        # Parent Query is the query on the parents space that resulted in the subspace.
        self.parentQuery = None

    def getVarNames(self):
        return self.fieldList

    def getValues(self, varName):
        fieldInd = self.fieldIndex[varName]
        isDisc = self.isDiscrete(varName)
        if not isDisc:
            return ['_many_']
        fieldType = self.fieldTypes[fieldInd]
        if fieldType == 's':
            dict = self.stringMap[varName]
            vals = list(dict.keys())
        else:
            vals = list(set(self.ds[varName]))
        vals.sort()
        return vals


    def convertToNumeric(self, varName, vals):
        unique = list(set(vals))
        unique.sort()
        dict = self.stringMap[varName]
        dictR = self.stringMapR[varName]
        numTag = 1
        for item in unique:
            dict[item] = numTag
            dictR[numTag] = item
            numTag += 1
        numVals = []
        for val in vals:
            numVal = dict[val]
            numVals.append(numVal)
        return numVals

    def getAgg(self):
        aData = self.aData
        numObs = aData.shape[1]  # Number of observations
        if numObs > 0:
            mins = self.aData.min(1)
            maxs = self.aData.max(1)
            means = self.aData.mean(1)
            stds = self.aData.std(1)
        outDict = {}
        if numObs:
            for i in range(self.aData.shape[0]):
                fieldName = self.fieldList[i]
                aggs = (mins[i], maxs[i], means[i], stds[i])
                outDict[fieldName] = aggs
        return outDict

    def getNumValue(self, varName, strVal):
        try:
            dict = self.stringMap[varName]
        except:
            return 0
        try:
            numVal = dict[strVal]
        except:
            return 0
        return numVal

    def calcDiscSpecs(self):
        discSpecs = []
        for i in range(len(self.fieldList)):
            var = self.fieldList[i]
            if self.N > 0:
                minV = np.min(self.aData[i])
                maxV = np.max(self.aData[i])
            else:
                minV = 0
                maxV = 0
            isDiscrete = var in self._discreteVars
            if isDiscrete:
                field = self.aData[i]
                vals, counts = np.unique(field, return_counts = True)
                nBins = len(vals)
                binStarts = vals
                hist = np.zeros((len(binStarts),))
                for j in range(len(vals)):
                    count = counts[j]
                    hist[j] = count
                vals2 = list(vals) + [vals[-1] + 1.0]
                edges = np.array(vals2)
            else:
                if self.N < 100:
                    nBins = 10
                else:
                    nBins = int(self.density * math.sqrt(self.N))
                field = np.asarray(self.aData[i], 'float64')
                hist, edges = np.histogram(field, nBins)
            discSpecs.append((nBins, minV, maxV, edges, hist, isDiscrete))
        return discSpecs

    def fixupDiscSpecs(self, discSpecs):
        # Recompute the histograms
        outSpecs = []
        for i in range(len(discSpecs)):
            discSpec = discSpecs[i]
            bins, min, max, edges, hist, isDiscrete = discSpec
            if isDiscrete:
                field = self.aData[i]
                vals, counts = np.unique(field, return_counts = True)
                countDict = {}
                for i in range(len(vals)):
                    val = vals[i]
                    count = counts[i]
                    countDict[val] = count
                outHist0 = []
                for i in range(bins):
                    edge = edges[i]
                    try:
                        count = countDict[edge]
                    except:
                        count = 0
                    outHist0.append(count)
                outHist = np.array(outHist0)
                outSpecs.append((bins, min, max, edges, outHist, True))
            else:
                # Regenerate histogram.  The other data should use the original
                newHist, newEdges = np.histogram(self.aData[i], bins, (min, max))
                outSpecs.append((bins, min, max, edges, newHist, False))
        return outSpecs

    # Unused
    def discretize(self):
        discretized = np.copy(self.aData)
        for i in range(len(self.fieldList)):
            field = self.aData[i]
            edges = self.discSpecs[i][3]
            dField = np.digitize(field, edges[:-1]) - 1
            discretized[i] = dField
        return discretized

    def toOriginalForm(self, discretized):
        data = {}
        for f in range(len(self.fieldList)):
            fieldName = self.fieldList[f]
            fieldVals = list(discretized[f, :])
            data[fieldName] = fieldVals
        return data

    # Unused
    def getMidpoints(self, field):
        indx = self.fieldIndex[field]
        dSpec = self.discSpecs[indx]
        edges = dSpec[3]
        isDiscrete = dSpec[5]
        mids = []
        if isDiscrete:
            for i in range(len(edges) - 1):
                mids.append(edges[i])
        else:
            for i in range(len(edges) - 1):
                mids.append((edges[i] + edges[i+1]) / 2)
        return mids

    # Unused
    def pdfToProbArray(self, pdf):
        vals = []
        for bin in pdf:
            prob = bin[3]
            vals.append(prob)
        outArray = np.array(vals)
        return outArray

    def fieldStats(self, field):
        return self.fieldAggs[field]

    def cardinality(self, rvName):
        vals = self.ds[rvName]
        return len(set(vals))

    def _isDiscreteVar(self, rvName):
        cardinality = self.cardinality(rvName)
        vals = self.ds[rvName]
        if not vals or type(vals[0]) == type('') or cardinality < 50:
            return True
        if cardinality > MAX_DISCRETE_VALS or cardinality > sqrt(self.N) * self.density:
            return False
        return True
    
    def _getDiscreteVars(self):
        discreteVars = []
        for var in self.fieldList:
            if var in self.categoricalVars or self._isDiscreteVar(var):
                # Note: All categorical vars are discrete.
                discreteVars.append(var)
        return discreteVars

    def isDiscrete(self, rvName):
        indx = self.fieldIndex[rvName]
        dSpec = self.discSpecs[indx]
        isDisc = dSpec[5]
        return isDisc

    def isCategorical(self, rvName):
        return rvName in self.categoricalVars

    def isStringVal(self, rvName):
        return rvName in self.stringMap.keys()

    def strToNum(self, rvName, strval):
        if rvName in self.stringMap.keys():
            dict = self.stringMap[rvName]
            try:
                numval = dict[strval]
            except:
                numval = None
            return numval
        else:
            return None
    
    def numToStr(self, rvName, numval):
        if rvName in self.stringMapR.keys():
            dict = self.stringMapR[rvName]
            try:
                strval = dict[numval]
            except:
                strval = None
            return strval
        else:
            return None

    # Not used
    def getBucketVals(self, field):
        indx = self.fieldIndex[field]
        dSpec = self.discSpecs[indx]
        bucketCount = dSpec[0]
        return range(bucketCount)

    def SubSpace(self, givensSpec, minPoints=None, maxPoints=None, power=None,
                        density=None, discSpecs=None, fixDistr=False, cMethod=None):
        """ Return a new ProbSpace object representing a sub-space of the current
            probability space.
            The returned object represents the multivariate joint probability space
            of the original space given a set of conditions.
            That is: P(<all variables> | givensSpec).
            The data is filtered by the givensSpec, and the resulting conditional
            distribution is returned.
            For discrete variables, or filters specified as (varName, high, low),
            exact filtering is done.
            For continuous variables specified as (varName, value), progressive
            filtering is used.  Since the probability of any continuous value is
            zero, the specification (varName, value) is converted to a small
            range (varName, value - delta, value + delta).  This range is iteratively
            adjusted so that a "reasonable" number of data points.  By default,
            that "reasonable" number is between 100 and 1000 data points -- enough
            to produce a reliably measurable distribution.   IF desired, different
            limits can be provided using the minPoints and maxPoints parameters.
            For example, by using minPoints = 1, and maxPoints = 10, one could
            request a small number of samples that are the closest to the requested
            values.  Note that sample counts cannot be managed exactly, so it is
            possible to receieve sample counts smaller or greater than the requested
            range.
        """
        if power is None:
            power = self.power
        if density is None:
            density = self.density
        if cMethod is None:
            cMethod = self.cMethod
        #print('givens = ', givensSpec)
        #print('minPoints, maxPoints, self.N = ', minPoints, maxPoints, self.N)
        filtDat, parentProb, finalQuery = self.filter(givensSpec, minPoints=minPoints, maxPoints=maxPoints)
        # Prepare the textInfo vars to transfer to the subspace
        textInfo = (self.categoricalVars, self.fieldTypes, self.stringMap, self.stringMapR)
        if fixDistr:
            newPS = ProbSpace(filtDat, power = power, density = density, 
                discSpecs = discSpecs, cMethod = cMethod, textInfo = textInfo)
        else:
            newPS = ProbSpace(filtDat, power = power, density = density,
                cMethod = cMethod, textInfo = textInfo)
        newPS.parentProb = parentProb
        newPS.parentQuery = finalQuery
        if DEBUG:
            print('ProbSpace.Subspace:  Query = ', finalQuery, ', N = ', newPS.N)
        return newPS

    def filter(self, filtSpec, minPoints=None, maxPoints=None):
        """ Filter the data based on a set of filterspecs: [filtSpec, ...]
            filtSpec := (varName, value) or (varName, lowValue, highValue)
            Returns a data set in the original {varName:[varData], ...}
            dictionary format.
            See FilteredSpace documentation (above) for details.
        """
        filtdata, parentProb, finalQuery = self.filterDat(filtSpec, minPoints, maxPoints)
        # Now convert to orginal format, with only records that passed filter
        outData = self.toOriginalForm(filtdata)
        return outData, parentProb, finalQuery

    def filterDat(self, filtSpec, minPoints=None, maxPoints=None, adat = None):
        """ Filter the data in its array form and return a filtered array.
            See FilteredSpace documentation (above) for details.
        """
        if DEBUG:
            print('ProbSpace.filterDat: filtSpec = ', repr(filtSpec))
        maxAttempts = 8
        if self.N > 1:
            maxDelta = .4 / log(self.N, 10)
        else:
            maxDelta = .4
        #print('filterDat: maxDelta = ', maxDelta)
        delta = maxDelta / 2.0
        Dquery = len(filtSpec) + 1
        Nfilt = self.N**(1 / Dquery)
        minPoints_default = max([Nfilt*.8, 20])
        maxPoints_default = max([Nfilt*1.2, self.N / 10])
        if minPoints is None:
            minPoints = minPoints_default
        if maxPoints is None:
            maxPoints = maxPoints_default
        if adat is None:
            adat = self.aData
        progressive = False
        # Reorganize the filters for fast runtime.
        # Four types of filters:
        # - P -- Progressive (P, var, fieldInd, value)
        # - V -- Exact Value (V, var, fieldInd, std, value)
        # - R -- Range (R, var, fieldInd, low, high)
        # - L -- List (L, var, fieldInd, val1, val2, ... , valK)
        filtSpec2 = [] # Reorganized filters
        for filt in filtSpec:
            var = filt[0]
            filtVal1 = filt[1]
            fieldInd = self.fieldIndex[var]
            if var in self.categoricalVars or len(filt) > 3 or type(filtVal1) in [type((0,)), type([]), type('')]:
                # List type
                filtType = 'L'
                if type(filtVal1) in [type([]), type((0,))]:
                    args = tuple(filtVal1)
                else:
                    args = filt[1:]
                if self.fieldTypes[fieldInd] == 's' and type(args[0]) == type(''):
                    # Convert values from strings to numeric tags
                    dict = self.stringMap[var]
                    args = tuple([dict[val] for val in list(args)])
            elif len(filt) == 2:
                # Exact value 
                if not self.isDiscrete(var):
                    # Not a discrete var.  We can do progressive filtering
                    filtType = 'P'
                    aggs = self.fieldAggs[var]  # Field aggregates
                    std = aggs[3] # Standard deviation
                    args = (std, filtVal1)
                    progressive = True
                else:
                    # Discrete.  Just match exactly
                    filtType = 'V'
                    args = (filtVal1,)
            else:
                # It is a range.  Exactly three field in filtSpec
                filtType = 'R'
                low = filt[1]
                high = filt[2]
                if low is None or high is None:
                    dSpec = self.discSpecs[fieldInd]
                    if low is None:
                        varMin = dSpec[1]
                        low = varMin
                    if high is None:
                        varMax = dSpec[2]
                        high = varMax + .0001
                args = (low, high)
            filtSpec2.append((filtType, var, fieldInd) + args)
        #print('filtSpec2 = ', filtSpec2)
        if progressive:
            attempts = maxAttempts
        else:
            attempts = 1
        for attempt in range(attempts):
            remRecs = []
            # Compose the final filter for this run.
            finalFilter = []
            for filt in filtSpec2:
                fType = filt[0]
                var = filt[1]
                args = filt[3:]
                if fType != 'P':
                    # Not progressive
                    finalFilter.append((var,) + args)
                else:
                    # Progressive
                    std = args[0]
                    sDelta = delta * std # Scaled delta
                    cmpval = args[1]
                    low = cmpval - sDelta
                    high = cmpval + sDelta
                    finalFilter.append((var, low, high))
            for i in range(self.N):
                include = True
                for filt in filtSpec2:
                    fType = filt[0]
                    var = filt[1]
                    fieldInd = filt[2]
                    args = filt[3:]
                    val = adat[fieldInd, i]
                    if fType == 'L':
                        if val not in args:
                            include = False
                            break
                    elif fType == 'P':
                        std = args[0]
                        cmpval = args[1]
                        sDelta = delta * std # Scaled delta
                        low = cmpval - sDelta
                        high = cmpval + sDelta
                        if val < low or val >= high:
                            include = False
                            break
                    elif fType == 'V':
                        cmpval = args[0]
                        if val != cmpval:
                            include = False
                            break
                    elif fType == 'R':
                        low = args[0]
                        high = args[1]
                        if val < low or val >= high:
                            include = False
                            break
                    else:
                        assert False, 'ProbSpace.filterDat.  Illegal filter type = ' + str(fType)
                if not include:
                    remRecs.append(i)
            remaining = self.N - len(remRecs)
            #print('attempt = ', attempt, ', remaining = ', remaining, ', delta = ', delta)
            targetVal = maxPoints * .5
            midpoint = (minPoints + maxPoints) / 2.0
            damping = (1/(attempt+1))
            minFactor = 1.1**damping
            maxFactor = 5**damping
            if remaining > 0:
                ratio = (midpoint / remaining)**damping
            else:
                ratio = maxFactor
            #print('ratio1 = ', ratio)
            #print('minFactor, maxFactor = ', minFactor, maxFactor)
            if ratio >= 1 and ratio < minFactor:
                ratio = minFactor
            elif ratio < 1 and ratio > (1/minFactor):
                ratio = 1/minFactor
            if ratio >= maxFactor:
                ratio = maxFactor
            elif ratio < (1/maxFactor):
                ratio = 1/maxFactor
            #print('ratio2 = ', ratio)
            if remaining < minPoints or remaining > maxPoints:
                # Outside of range.  Scale up or down.
                newDelta = delta * ratio
            else:
                # We're in range.  Set newDelta to 0 to exit loop
                newDelta = 0
            if newDelta > maxDelta:
                newDelta = maxDelta
            if newDelta == 0 or delta >= maxDelta:
                # If we're in range or if we've exceeded maxDelta, break out of loop.
                break 
            else:
                # Continue in loop with a new delta.
                delta = newDelta
        if DEBUG and progressive:
            print('attempt = ', attempt, ', delta = ', delta, ', maxDelta = ', maxDelta, ', remaining = ', remaining, ', minPoints, maxPoints = ', minPoints, maxPoints)
            #print('finalQuery = ', filtSpec2, ', parentProb = ', remaining/self.N, ', parentN = ', self.N)
            pass
        # Remove all the non included rows
        filtered = np.delete(adat, remRecs, 1)
        parentProb = filtered.shape[1] / float(self.N)
        finalQuery = finalFilter
        return filtered, parentProb, finalQuery
        # End of filterDat

    def filterDatOld(self, filtSpec, minPoints=None, maxPoints=None, adat = None):
        """ Filter the data in its array form and return a filtered array.
            See FilteredSpace documentation (above) for details.
        """
        if DEBUG:
            print('ProbSpace.filterDat: filtSpec = ', repr(filtSpec))
        maxAttempts = 8
        if self.N > 1:
            maxDelta = .4 / log(self.N, 10)
        else:
            maxDelta = .4
        #print('filterDat: maxDelta = ', maxDelta)
        delta = maxDelta / 2.0
        Dquery = len(filtSpec) + 1
        Nfilt = self.N**(1 / Dquery)
        minPoints_default = max([Nfilt*.8, 20])
        maxPoints_default = max([Nfilt*1.2, self.N / 10])
        if minPoints is None:
            minPoints = minPoints_default
        if maxPoints is None:
            maxPoints = maxPoints_default
        if adat is None:
            adat = self.aData
        
        # Determine if we are doing progressive filtering on at least one variable
        progressive = False
        for filt in filtSpec:
            if len(filt) == 2:
                var, val = filt
                if not self.isDiscrete(var):
                    progressive = True
                    break
        if progressive:
            attempts = maxAttempts
        else:
            attempts = 1
        for attempt in range(attempts):
            # Fix up progressively filtered specs (i.e. non discrete, 2-tuples)
            # to filter on value - delta to value + delta
            filtSpec2 = []
            for filt in filtSpec:
                var = filt[0]
                if not progressive or self.isDiscrete(var) or type(filt[1]) in [type((0,)), type([])] or len(filt) >= 3:
                    filtSpec2.append(filt)
                else:
                    # Progressive
                    val = filt[1]
                    aggs = self.fieldAggs[var]  # Field aggregates
                    std = aggs[3] # Standard deviation
                    sDelta = delta * std # Scaled delta
                    filtSpec2.append((var, val - sDelta, val + sDelta))
            remRecs = []
            for i in range(self.N):
                include = True
                for filt in filtSpec2:
                    var = filt[0]
                    filtVal1 = filt[1]
                    if var in self.categoricalVars or len(filt) > 3 or type(filtVal1) in [type((0,)), type([]), type('')]:
                        # We'll interpret the filter as a list of values, rather than a range if: variable is categorical
                        # or the second term in the filter is a list, or if the filter values contain strings.
                        if type(filtVal1) in [type([]), type((0,))]:
                            filt = (var,) + tuple(filtVal1)
                            filtVal1 = filt[1]
                        fieldInd = self.fieldIndex[var]
                        if self.fieldTypes[fieldInd] == 's' and type(filtVal1) == type(''):
                            # Convert values from strings to numeric tags
                            dict = self.stringMap[var]
                            filtVals = [dict[val] for val in list(filt[1:])]
                        else:
                            filtVals = list(filt[1:])
                        val = adat[fieldInd, i]
                        if val not in filtVals:
                            include = False
                            break
                    else:
                        if len(filt) == 2:
                            var, targetVal = filt
                            if type(targetVal) == type((0,)):
                                targetVal = val[0]
                            fieldInd = self.fieldIndex[var]
                            val = adat[fieldInd, i]
                            if val != targetVal:
                                include = False
                                break
                        else:
                            var, low, high = filt
                            fieldInd = self.fieldIndex[var]
                            dSpec = self.discSpecs[fieldInd]
                            varMin = dSpec[1]
                            varMax = dSpec[2]
                            if low is None:
                                low = varMin
                            if high is None:
                                high = varMax + .0001
                            val = adat[fieldInd, i]
                            if val < low or val >= high:
                                include = False
                                break
                if not include:
                    remRecs.append(i)
            remaining = self.N - len(remRecs)
            #print('attempt = ', attempt, ', remaining = ', remaining, ', delta = ', delta)
            targetVal = maxPoints * .5
            midpoint = (minPoints + maxPoints) / 2.0
            damping = (1/(attempt+1))
            minFactor = 1.1**damping
            maxFactor = 5**damping
            if remaining > 0:
                ratio = (midpoint / remaining)**damping
            else:
                ratio = maxFactor
            #print('ratio1 = ', ratio)
            #print('minFactor, maxFactor = ', minFactor, maxFactor)
            if ratio >= 1 and ratio < minFactor:
                ratio = minFactor
            elif ratio < 1 and ratio > (1/minFactor):
                ratio = 1/minFactor
            if ratio >= maxFactor:
                ratio = maxFactor
            elif ratio < (1/maxFactor):
                ratio = 1/maxFactor
            #print('ratio2 = ', ratio)
            if remaining < minPoints or remaining > maxPoints:
                # Outside of range.  Scale up or down.
                newDelta = delta * ratio
            else:
                # We're in range.  Set newDelta to 0 to exit loop
                newDelta = 0
            if newDelta > maxDelta:
                newDelta = maxDelta
            if newDelta == 0 or delta >= maxDelta:
                # If we're in range or if we've exceeded maxDelta, break out of loop.
                break 
            else:
                # Continue in loop with a new delta.
                delta = newDelta
        if DEBUG and progressive:
            print('attempt = ', attempt, ', delta = ', delta, ', maxDelta = ', maxDelta, ', remaining = ', remaining, ', minPoints, maxPoints = ', minPoints, maxPoints)
            #print('finalQuery = ', filtSpec2, ', parentProb = ', remaining/self.N, ', parentN = ', self.N)
            pass
        # Remove all the non included rows
        filtered = np.delete(adat, remRecs, 1)
        #print('filtered.shape = ', filtered.shape)
        finalQuery = filtSpec2
        parentProb = filtered.shape[1] / float(self.N)
        return filtered, parentProb, finalQuery
        # End of filterDatOld

    # Not used
    def binToVal(self, field, bin):
        indx = self.fieldIndex[field]
        dSpec = self.discSpecs[indx]
        min = dSpec[1]
        max = dSpec[2]
        val = (min + max) / 2
        return val

    def makeHashkey(self, targetSpec, givenSpec, power, cMethod=None):
        if type(targetSpec) == type([]):
            targetSpec = tuple(targetSpec)
        if type(givenSpec) == type([]):
            givenSpec = tuple(givenSpec)
        if cMethod is not None:
            hashKey = (targetSpec, givenSpec, power, cMethod)
        else:
            hashKey = (targetSpec, givenSpec, power)
        return hashKey

    def normalizeSpecs(self, inSpecs):
        """
        Normalize a target or conditional spec so that it is always:
        [spec], where spec is a 1, 2, or 3 tuple (varName,), (varName, val),
        or (varName, lowVal, highVal).
        """
        if inSpecs is None:
            # Return an empty list for None
            return []
        if type(inSpecs) == type([]):
            # List.
            pass
        else:
            # Not in list form.  Put it in a list
            inSpecs = [inSpecs]
        outSpecs = []
        for inSpec in inSpecs:
            if type(inSpec) == type((1,)):
                # It's a tuple. We're done
                outSpecs.append(inSpec)
            else:
                # Must be a bare variable.  Put in a tuple.
                outSpecs.append((inSpec,))
        # Sort the specs so thay are in a consistent order (for cacheing).
        outSpecs.sort()
        return outSpecs

    def specsAreBound(self, inSpecs):
        """
        inSpecs must be a nomalized spec.
        """
        for spec in inSpecs:
            if len(spec) == 1:
                return False
        return True

    def specsAreContinuous(self, inSpecs):
        """
        Returns true if all of the variables in inSpecs are continuous
        (i.e. not discrete).  inSpecs should have been previously 
        normalized via normalizeSpecs().
        """
        for spec in inSpecs:
            var = spec[0]
            if self.isDiscrete(var):
                return False
        return True

    def getCondSpace(self, givenSpecs, Dtarg=1):
        """
        Get a new ProbSpace filtered by the bound givenSpecs.  This is
        the conditional space.
        """
        Dfilt = len(givenSpecs)
        Dquery = Dtarg + Dfilt
        Ntarg = self.N**(Dtarg / Dquery)
        minP = .8 * Ntarg
        maxP = 1.2 * Ntarg
        ss = self.SubSpace(givenSpecs, minPoints=minP, maxPoints=maxP)
        if DEBUG:
            print('ProbSpace.getCondSpace: ssN, min, max, query = ', ss.N, minP, maxP, ss.parentQuery)
        return ss

    def E(self, targetSpecs, givenSpecs=None, power=None, cMethod=None, smoothness=1.0):
        """ Returns the expected value (i.e. mean) of the distribution
            of a single variable given a set of conditions.  This is
            a convenience function equivalent to:
                distr(target, givensSpec).E()

            - targetSpecs is a single variable name.
            - givenSpecs is a conditional specification (see distr below
                for format)
            -cMethod provides alternate methods for computing conditional expectations, overriding the
                class level selection:
                - D-Prob ('d' or 'd!') -- Discretization method.  This is the fastest and most flexible.
                - J-Prob ('j') -- Multivariate Kernel based modeling of joint probability.  This is the most
                                    accurate when dimensionality is high or data is scarce.  Continuous data
                                    only.
                - U-Prob ('u') -- Hybrid of D-Prob and J-Prob.  This is generally more accurate than D-Prob
                                    but not as accurate as J-Prob when dimensionality is high or data is scarce.
                                    Performance is comparable to D-Prob. Continuous data only.
                - ML-Prob ('ml') -- Using Machine Learning algorithm to regress on the target variable. Currently,
                                    we use Random Forest, which shows the best performance. The parameter power will
                                    determine how many trees we will use, ranging from 20 to 200 corresponding to power
                                    from 1 to 10.
            - smoothness (range: (0, 2]) -- Applies only to J-Prob or U-Prob. Determines the smoothness of the
                                    kernel used.  Lower values can be used to increase precision for complex
                                    distributions with sufficient data.  Higher values provide a smoother
                                    result with less variance.  Default 1.0.
        """
        if power is None:
            power = self.power
        if cMethod is None:
            cMethod = self.cMethod
        targetSpecs = self.normalizeSpecs(targetSpecs)
        givenSpecs = self.normalizeSpecs(givenSpecs)
        if DEBUG:
            print('ProbSpace.E: E(' , targetSpecs, '|', givenSpecs , ')', ', power = ', power)
        assert len(targetSpecs) == 1, 'ProbSpace.E: target must be singular.  Got ' + str(targetSpecs)
        assert not self.specsAreBound(targetSpecs), 'ProbSpace.E: target must be unbound (i.e. a bare variable name or 1-tuple).  Got ' + str(targetSpecs)
        target = targetSpecs[0][0] # Single bare variable
        cacheKey = self.makeHashkey(target, givenSpecs, power, cMethod)
        if cacheKey in self.expCache.keys():
            return self.expCache[cacheKey]
        if not givenSpecs:
            # Unconditional Expectation
            findx = self.fieldIndex[target]
            dat = self.aData[findx]
            if len(dat) == 0:
                # No data.  We can't know the expected value.  Return None.
                result = 0
            elif target in self.categoricalVars:
                # For categoricals, the expected value is the mode.
                vals, counts = np.unique(self.aData[findx], return_counts=True)
                idx = np.where(counts == np.amax(counts))
                # The result of where is an array inside a 1-tuple.  Not sure why
                # the outer tuple.  We pick the first value with max-count.
                result0 = float(vals[idx[0][0]])
                # Got the most frequent numerical value.  Do we need to convert
                # it to a category string?
                if self.fieldTypes[findx] == 's':
                    # String type.  Convert it.
                    dict = self.stringMapR[target]
                    result = dict[result0]
                else:
                    result = result0
            else:
                # For all methods, this is the best unconditional expectation
                # for numeric data.
                result = np.mean(self.aData[findx])
        else:
            #if self.isDiscrete(target) or not self.specsAreContinuous(givenSpecs):
                # If the target is discrete or any of the givens are discrete, we will
                # need to use D-Prob.
            #    if not (self.isCategorical(target) and cMethod == 'ml'):
            #        cMethod = 'd'
            if cMethod[0] == 'd':  # d or d!
                #print('***** Discrete -- D-Prob')
                result = self.Edisc(target, givenSpecs, power)
            elif cMethod == 'u':  #uprob
                #print('***** U-Prob')
                result = self.Eup(target, givenSpecs, power, smoothness=smoothness)
            elif cMethod == 'j':
                #print('***** J-Prob')
                result = self.Ejp(target, givenSpecs, power, smoothness=smoothness)
            elif len(givenSpecs) > 1 or self.isCategorical(target):
                result = self.Eml(target, givenSpecs, power)
            else:
                result = self.Edisc(target, givenSpecs, power)
        self.expCache[cacheKey] = result
        if DEBUG:
            print('ProbSpace.E: E(' , targetSpecs, '|', givenSpecs , '), Result = ', result)
        return result

    def Eml(self, target, givenSpecs, power):
        def fixupSpecs(givenSpecs):
            mustFilter = []
            fixed = []
            for spec in givenSpecs:
                var = spec[0]
                if len(spec) == 1:
                    fixed.append(spec)
                elif len(spec) == 2:
                    val1 = spec[1]
                    if type(val1) == type(''):
                        # Looking for a single categorical (string) value.  Map to numeric
                        map = self.stringMap[var]
                        val1 = map[val1]
                    fixed.append((var, val1))
                elif len(spec) == 3:
                    val1, val2 = spec[1:]
                    if self.isCategorical(var):
                        # Looking for one of several categorical values.  Can't use ML.
                        mustFilter.append(spec)
                    else:
                        # Handle the case where range is [-inf, val] or [val, +inf]
                        if val1 is None:
                            indx = self.fieldIndex[var]
                            minv = float(np.min(self.aData[indx]))
                            cond = (minv + spec[2]) * .5
                        elif val2 is None:
                            indx = self.fieldIndex[var]
                            maxv = float(np.max(self.aData[indx]))
                            cond = (spec[1] + maxv) * .5
                        else:
                            # If a range, use the midpoint of the range for ML.
                            cond = (spec[1] + spec[2]) * .5
                        fixed.append((var, cond))
                else:
                    # Must be a multi value categorical filter.  Can't use ML.
                    mustFilter.append(spec)
            return mustFilter, fixed
        mustFilter, givenSpecs = fixupSpecs(givenSpecs)
        if mustFilter:
            ss = self.SubSpace(mustFilter)
        else:
            ss = self
        condSpecs, filtSpecs = ss.separateSpecs(givenSpecs)
        n_estimators = min([power * 20, 200])
        cacheVars = mustFilter + [var[0] for var in filtSpecs] + [var[0] for var in condSpecs]
        cacheKey = (target, tuple(cacheVars))
        
        if not condSpecs:
            # Straight (bound) conditioning
            filtVars = [spec[0] for spec in filtSpecs]
            filtVals = [spec[1] for spec in filtSpecs]
            if cacheKey in self.mlmodelCache.keys():
                reg = self.mlmodelCache[cacheKey]
            else:
                if self.isCategorical(target):
                    reg = RandomForestClassifier(n_estimators=n_estimators)
                else:
                    reg = RandomForestRegressor(n_estimators=n_estimators)
                y = np.array(ss.ds[target])
                X = np.array([ss.ds[filtVar] for filtVar in filtVars]).transpose()
                reg.fit(X, y)
                self.mlmodelCache[cacheKey] = reg
            result = reg.predict(np.array(filtVals).reshape(1, -1))[0]
        else:
            # Conditionalization and possibly conditioning as well.
            # Conditionalize on all indicated variables. I.e.,
            # SUM(P(filteredY | Z=z) * P(Z=z)) for all z in Z
            condFiltSpecs = self.getCondSpecs(condSpecs, power=power)
            accum = 0.0
            allProbs = 0.0
            # Try to get model from cache.  Otherwise create it.
            filtVars = [spec[0] for spec in filtSpecs] + [spec[0] for spec in condSpecs]
            if cacheKey in self.mlmodelCache.keys():
                reg = self.mlmodelCache[cacheKey]
            else:
                if self.isCategorical(target):
                    reg = RandomForestClassifier(n_estimators=n_estimators)
                else:
                    reg = RandomForestRegressor(n_estimators=n_estimators)
                y = np.array(ss.ds[target])
                X = np.array([ss.ds[condVar] for condVar in filtVars]).transpose()
                reg.fit(X, y)
                self.mlmodelCache[cacheKey] = reg
            for cf in condFiltSpecs:
                # Shouldn't need any filtering at this point.  We just want to fixup the specs
                unused, cf2 = fixupSpecs(cf)
                allSpecs = filtSpecs + cf2
                filtVals = [spec[1] for spec in allSpecs]
                exp = reg.predict(np.array(filtVals).reshape(1, -1))[0]
                if exp is None:
                    continue
                probZ = self.P(cf)
                #print('probZ = ', probZ, ', exp = ', exp, condVals)
                if probZ == 0:
                    # Zero probability -- don't bother accumulating
                    continue
                accum += exp * probZ
                allProbs += probZ
            result = accum / allProbs
        return result

    def Edisc(self, target, givenSpecs, power):
        # Conditional Expectation
        condSpecs, filtSpecs = self.separateSpecs(givenSpecs)
        if not condSpecs:
            if DEBUG:
                print('Edisc: filtSpecs = ', filtSpecs)
            # Straight (bound) conditioning
            Dtarg = 1
            ss = self.getCondSpace(filtSpecs)
            #print('No control: ss.N = ', ss.N)
            if ss.N <= 10:
                # No points in this interval.  Use kernel approximation
                # to estimate the expectation.
                result = self.Ejp(target, filtSpecs, power, smoothness=1)
            else:
                result = ss.E(target, power=power)
        else:
            # Conditionalization and possibly conditioning as well.
            # Conditionalize on all indicated variables. I.e.,
            # SUM(P(filteredY | Z=z) * P(Z=z)) for all z in Z.
            # First, we filter on the bound conditions (if any), then conditionalize on the reduced set
            if filtSpecs:
                ss = self.getCondSpace(filtSpecs, Dtarg = len(condSpecs) + 1)
                #print('ss.N = ', ss.N)
            else:
                ss = self
            if ss.N <= 2:
                return None
            condFiltSpecs = self.getCondSpecs(condSpecs, power=power, effN=ss.N)
            #print('condSpecs = ', condSpecs, ', condFiltSpecs = ', condFiltSpecs)
            accum = 0.0
            allProbs = 0.0
            for cf in condFiltSpecs:
                # Create a new subspace filtered by both the bound and unbound conditions
                # Note that progressive filtering will be used for the unbound conditions.
                # probYgZ is P(Y | Z=z) e.g., P(Y | X=1, Z=z)
                exp = ss.E(target, cf, power=power)
                # If expectation is None it means we can't find any points, so we have no
                # knowledge of the expectation.  Skip.
                if exp is None:
                    continue
                probZ = self.P(cf, power=power)
                #print('probZ = ', probZ, ', exp = ', exp,  ', ss.N = ', ss.N, ', cf = ', cf)
                if probZ == 0:
                    # Zero probability -- don't bother accumulating
                    continue
                accum += exp * probZ
                allProbs += probZ
            if allProbs > 0:
                result = accum / allProbs
            else:
                result = None
        return result

    def Ejp(self, target, givenSpecs, power, smoothness=1.0):
        # Conditional Expectation
        condSpecs, filtSpecs = self.separateSpecs(givenSpecs)

        if not condSpecs:
            # Straight (bound) conditioning
            # We need to saparate equality filters from others. If there
            # are non-equality filters, we need to first filter the data
            # and then apply the RKHS.  We treat range data as equality,
            # using the midpoint as an equality value.
            if DEBUG:
                print('Ejp: filtSpecs = ', filtSpecs)
           
            eqvals = []
            eqspecs = []
            neqspecs = []

            for filtSpec in filtSpecs:
                if len(filtSpec) == 2:
                    if type(filtSpec[1] != type((1,))) and not self.isCategorical(filtSpec[0]):
                        if type(filtSpec[1]) == type(''):
                            # String val.  Convert to number.
                            val = self.strToNum(filtSpec[0], filtSpec[1])
                        else:
                            val = filtSpec[1]
                        eqvals.append(val)
                        eqspecs.append(filtSpec)
                    else:
                        neqspecs.append(filtSpec)
                else:
                    if filtSpec[1] is not None and filtSpec[2] is not None:
                        eqvals.append((filtSpec[1] + filtSpec[2]) / 2.0)
                        eqspecs.append(filtSpec)
                    else:
                        if filtSpec[1] is None:
                            val = filtSpec[2]
                        else:
                            val = filtSpec[1]
                        eqvals.append(val)
                        eqspecs.append(filtSpec)
            if neqspecs:
                ss = self.getCondSpace(neqspecs)
                if ss.N < 1:
                    # We have to punt here.  Ignore the filters, and continue.
                    # We need to return something, and zero is a bad answer.
                    print('Ejp: Punting -- Ignoring filters')
                    ss = self
            else:
                ss = self
            if not eqspecs:
                # Again we have to punt.
                #print('Ejp: Punting -- Returning unconditional mean.')
                return self.E(target, power=power)
            eqvars = [filtSpec[0] for filtSpec in eqspecs]
            print('Ejp: eqvars, eqvals  = ', eqvars, eqvals)
            # Try to get rkhs from cache.  Otherwise create it.
            cacheKey = (tuple(eqvars), smoothness)
            if cacheKey in self.rkhsCache.keys():
                R = self.rkhsCache[cacheKey]
            else:
                R = rkhsMV.RKHS(ss.ds, includeVars=eqvars, s=smoothness)
                self.rkhsCache[cacheKey] = R
            result = R.condE(target, eqvals)
        else:
            # Conditionalization and possibly conditioning as well.
            # Conditionalize on all indicated variables. I.e.,
            # SUM(P(filteredY | Z=z) * P(Z=z)) for all z in Z
            condFiltSpecs = self.getCondSpecs(condSpecs, power=power)
            accum = 0.0
            allProbs = 0.0
            condVars = [spec[0] for spec in filtSpecs] + [spec[0] for spec in condFiltSpecs[0]]
            # Try to get rkhs from cache.  Otherwise create it.
            cacheKey = (tuple(condVars), smoothness)
            if cacheKey in self.rkhsCache.keys():
                R = self.rkhsCache[cacheKey]
            else:
                R = rkhsMV.RKHS(self.ds, includeVars=condVars, s=smoothness)
                self.rkhsCache[cacheKey] = R
            for cf in condFiltSpecs:
                specs = filtSpecs + cf
                condVals = []
                for spec in specs:
                    if len(spec) == 2:
                        condVals.append(spec[1])
                    else:
                        condVals.append((spec[1] + spec[2]) / 2.0)
                exp = R.condE(target, condVals)
                if exp is None:
                    continue
                probZ = self.P(cf)
                #print('probZ = ', probZ, ', exp = ', exp, condVals)
                if probZ == 0:
                    # Zero probability -- don't bother accumulating
                    continue
                accum += exp * probZ
                allProbs += probZ
            result = accum / allProbs
        return result

    def Eup(self, target, givenSpecs, power, smoothness=1.0):
        # Conditional Expectation
        condSpecs, filtSpecs = self.separateSpecs(givenSpecs)

        if not condSpecs:
            # Straight (bound) conditioning
            if len(filtSpecs) > 1:
                filt = filtSpecs[0]
                filtSpecs = filtSpecs[1:]
                ss = self.getCondSpace([filt], Dtarg=len(filtSpecs)+1)
            else:
                ss = self
            if ss.N < 2:
                return None
            #print('ss.N = ', ss.N)
            filtVars = [filtSpec[0] for filtSpec in filtSpecs]
            filtVals = []
            for filtSpec in filtSpecs:
                if len(filtSpec) == 2:
                    filtVals.append(filtSpec[1])
                else:
                    filtVals.append((filtSpec[1] + filtSpec[2]) / 2.0)
            # Try to get rkhs from cache.  Otherwise create it.
            cacheKey = (tuple(filtVars), smoothness)
            if cacheKey in ss.rkhsCache.keys():
                R = ss.rkhsCache[cacheKey]
            else:
                R = rkhsMV.RKHS(ss.ds, includeVars=filtVars, s=smoothness)
                ss.rkhsCache[cacheKey] = R
            result = R.condE(target, filtVals)
        else:
            # Conditionalization and possibly conditioning as well.
            # Conditionalize on all indicated variables. I.e.,
            # SUM(P(filteredY | Z=z) * P(Z=z)) for all z in Z
            Dtarg = len(condSpecs) + 1
            ss = self.getCondSpace(filtSpecs, Dtarg=Dtarg)
            if ss.N < 2:
                return None
            #print('ss.N = ', ss.N)
            condFiltSpecs = self.getCondSpecs(condSpecs, power=power)
            accum = 0.0
            allProbs = 0.0
            condVars = [spec[0] for spec in condFiltSpecs[0]]
            # Try to get rkhs from cache.  Otherwise create it.
            cacheKey = (tuple(condVars), smoothness)
            if cacheKey in ss.rkhsCache.keys():
                R = ss.rkhsCache[cacheKey]
            else:
                R = rkhsMV.RKHS(ss.ds, includeVars=condVars, s=smoothness)
                ss.rkhsCache[cacheKey] = R
            for cf in condFiltSpecs:
                specs = cf
                condVals = []
                for spec in specs:
                    if len(spec) == 2:
                        condVals.append(spec[1])
                    else:
                        condVals.append((spec[1] + spec[2]) / 2.0)
                exp = R.condE(target, condVals)
                if exp is None:
                    # No points to evaluate.  Can't determine expectation.
                    continue
                probZ = self.P(cf)
                #print('probZ = ', probZ, ', exp = ', exp, condVals)
                if probZ == 0:
                    # Zero probability -- don't bother accumulating
                    continue
                accum += exp * probZ
                allProbs += probZ
            result = accum / allProbs
        return result

    def prob(self, targetSpecs, givenSpecs=None, power=None):
        """ Return the probability of a variable or (set of variables)
            attaining a given value (or range of values) given a set
            of conditionalities on other variables.
            'P' is an alias for prob.
            The basic form is the probability of Y given X or the probability
            of targetSpec given givenSpec, where X and Y represent events or lists
            of simultaneous events:
                P(Y=y | X=x) = P(targetSpec | givenSpec)

            targetSpec (target specification) defines the result to be returned
            (i.e. the event or set of events whose probability is to be determined).
            A target specification may take one of several forms:
            - 2-tuple (varName, value) for the probability
                of attaining a single value
            - 3-tuple: (varName, minValue, maxValue) indicating an interval:
                [minValue, maxValue) (i.e. minValue <= value < maxValue).
                minValue or maxValue may be None.  A minValue of None implies 
                -infinity.  A maxValue of None implies infinity.
            - list of either of the above or any combination.  In this case, the joint 
                probability of the events is returned.
            
            givenSpec (optional) has the same format as targetSpec with equivalent meanings
            for the givens.  A given specification supports one additional flavor which
            is a single variable name.  This means that that variable should be
            "conditionalized" on.  
            The three flavors (varName, 2-tuple, 3-tuple)
            may be mixed within a givenSpec, presented as a list of givens.

            Examples:
            - prob(('A', 1)) -- The probability that variable A takes on the value 1.
            - prob([('A', 1), ('B', 2)]) -- The (joint) probability that A is 1 and B is 2.
            - prob(('A', .1, .5)) -- The probability that A is in the range [.1, .5).
            - prob(('A', .1, .5), [('B', 0, 1), ('C', -1, None)] -- The probability that
                    A is on interval [.1, .5) given that B is on interval [0,1) and
                    C is on interval [-1, infinity).
            - prob(('A', .1, .5), [('B', 0, 1), ('C', -1, None), 'D'] -- The probability that
                variable A is on interval [.1, .5) given that B is on interval [0, 1) and
                    C is on interval [-1, infinity), conditionalized on D.
    
            Conditionalization is taking the probability weighted sum of the results for every value
            of the conditionalizing variable or combination of conditionalizing variables.
            For example:
            - P(A=1 | B=2, C) is: sum over all (C=c) values( P(A=1 | B=2, C=c) * P(C=c))
        """
        if power is None:
            power = self.power
        targetSpecs = self.normalizeSpecs(targetSpecs)
        givenSpecs = self.normalizeSpecs(givenSpecs)
        if DEBUG:
            print('ProbSpace.P: P(' , targetSpecs, '|', givenSpecs , ')')
        assert self.specsAreBound(targetSpecs), 'ProbSpace.P: target must be bound (i.e. include a value or value range).  Got ' + str(targetSpecs)
        cacheKey = self.makeHashkey(targetSpecs, givenSpecs, power)
        if cacheKey in self.probCache.keys():
            return self.probCache[cacheKey]
        if givenSpecs:
            # We have conditionals
            condSpecs, filtSpecs = self.separateSpecs(givenSpecs)
            if not condSpecs:
                # Straight (bound) conditioning
                Dtarg = 1
                ss = self.getCondSpace(filtSpecs)
                result=0.0
                if ss.N > 0:
                    result = ss.P(targetSpecs)
            else:
                # Conditionalization and possibly conditioning as well.
                # Conditionalize on all indicated variables. I.e.,
                # SUM(P(filteredY | Z=z) * P(Z=z)) for all z in Z.
                # First, we filter on the bound conditions (if any), then conditionalize on the reduced set
                if filtSpecs:
                    ss = self.getCondSpace(filtSpecs, Dtarg = len(condSpecs) + 1)
                else:
                    ss = self
                result = 0.0
                if ss.N > 0:
                    condFiltSpecs = self.getCondSpecs(condSpecs, power=power, effN=ss.N)
                    accum = 0.0
                    allProbs = 0.0
                    for cf in condFiltSpecs:
                        # Create a new subspace filtered by both the bound and unbound conditions
                        # Note that progressive filtering will be used for the unbound conditions.
                        # probYgZ is P(Y | Z=z) e.g., P(Y | X=1, Z=z)
                        p = ss.P(targetSpecs, cf)
                        # If expectation is None it means we can't find any points, so we have no
                        # knowledge of the expectation.  Skip.
                        probZ = self.P(cf)
                        #print('probZ = ', probZ, ', exp = ', exp,  ', ss.N = ', ss.N)
                        if probZ == 0:
                            # Zero probability -- don't bother accumulating
                            continue
                        accum += p * probZ
                        allProbs += probZ
                    result = accum / allProbs
        else:
            # Marginal probability
            ss = self
            if ss.N > 0:
                ss2 = ss.getCondSpace(targetSpecs)
                result = ss2.N / ss.N
            else:
                result = 0
        self.probCache[cacheKey] = result
        if DEBUG:
            print('ProbSpace.P: P(' , targetSpecs, '|', givenSpecs , ')', ', result = ', result)
        return result

    P = prob


    def distr(self, targetSpecs, givenSpecs=None, power=None):
        """Return a univariate probability distribution as a PDF (see pdf.py) for the random variable
           indicated by rvName.
           If givenSpec is provided, then will return the conditional distribution,
           otherwise will return the unconditional (i.e. marginal) distribution.
           This satisfies the following types of probability queries:
            - P(Y) -- (marginal) Probability distribution of Y
            - P(Y | X=x) -- Conditional probability
            - P(Y | X1=x1, ... ,Xk = xk) -- Multiple conditions
            - P(Y | X=x, Z) -- i.e. Conditionalize on Z
            - P(Y | X=x, Z1, ... Zk) -- Conditionalize on multiple variables

            targetSpecs is the name of the random variable whose distribution is requested.
            givenSpec (given specification) defines the conditions (givens) to
            be applied.
            A given specification may take one of several forms:
            - 2-tuple (varName, value) - Variable taking on a given value.
            - 3-tuple: (varName, minValue, maxValue) indicating an interval:
                [minValue, maxValue) (i.e. minValue <= value < maxValue).
                minValue or maxValue may be None.  A minValue of None implies 
                -infinity.  A maxValue of None implies infinity.
            - variable name: A variable to conditionalize on.
            - list of any of the above or any combination of above.

            Examples:P
            - distr('Y') -- The (marginal) probability of Y
            - distr('Y', [('X', 1)]) -- The probability of Y given X=1.
            - distr('Y', [('X', 1, 2)]) -- The probability of Y given 1 <= X < 2.
            - distr('Y', ('X', 1)) -- The probability of Y given X=1 (same as above)
            - distr('Y', [('X1', 1), ('X2', 0)]) - The probability of Y given X1 = 1, and X2 = 0
            - distr('Y', [('X', 1), 'Z']) -- The probability of Y given X = 1, conditionalized on Z

            Conditionalization is taking the probability weighted sum of the results for every value
            of the conditionalizing variable or combination of conditionalizing variables.
            For example:
            - P(Y | X=1, Z) is: sum over all (Z=z) values( P(Y | X=1, Z=z) * P(Z=z))
        """
        DISC_DISTS = False # If False, use full data for distributions.  Otherwise use binned data.
                          # Will be much slower and slightly more accurate if False.

        if power is None:
            power = self.power
        if self.N < 10:
            # Too few points to assess a distribution.
            return None
        targetSpecs = self.normalizeSpecs(targetSpecs)
        givenSpecs = self.normalizeSpecs(givenSpecs)
        assert len(targetSpecs) == 1, 'ProbSpace.distr: target must be singular.  Got ' + str(targetSpecs)
        assert not self.specsAreBound(targetSpecs), 'ProbSpace.distr: target must be unbound (i.e. a bare variable or 1-tuple).  Got ' + str(targetSpecs)
        rvName = targetSpecs[0][0]
        if DEBUG:
            print('ProbSpace.distr: P(' , rvName, '|', givenSpecs , ')')
        cacheKey = self.makeHashkey(rvName, givenSpecs, power)
        if cacheKey in self.probCache.keys():
            return self.probCache[cacheKey]
        isDiscrete = self.isDiscrete(rvName)
        indx = self.fieldIndex[rvName]
        dSpec = self.discSpecs[indx]
        bins = dSpec[0]

        if not givenSpecs:
            # Marginal (unconditional) Probability
            bins = dSpec[0]
            hist = list(dSpec[4])
            if not hist:
                hist = [0] * bins
            edges = list(dSpec[3])
            outHist = []
            for i in range(len(hist)):
                cnt = hist[i]
                if self.N > 0:
                    outHist.append(cnt / self.N)
                else:
                    outHist.append(0)
            pdfSpec = []
            for i in range(len(outHist)):
                start = edges[i]
                end = edges[i+1]
                pdfSpec.append((i, start, end, outHist[i]))
            if not DISC_DISTS:
                dat = self.aData[indx,:]
            else:
                dat = None
            outPDF = PDF(self.N, pdfSpec, isDiscrete=isDiscrete, data=dat, rvName=rvName)
        else:
            # Conditional Probability
            condSpecs, filtSpecs = self.separateSpecs(givenSpecs)
            if not condSpecs:
                # Nothing to conditionalize on.  We're computing a fully bound conditional (i.e. no free variables)
                #Dquery = len(filtSpecs) + 1
                ss = self.getCondSpace(filtSpecs)
                #Nfilt = self.N**(len(filtSpecs) / Dquery)
                #ss = self.SubSpace(filtSpecs)
                outPDF = ss.distr(rvName)
                #outPDF = self.boundCondition(rvName, filtSpecs)
            else:
                # Conditionalize on all indicated variables. I.e.,
                # SUM(P(filteredY | Z=z) * P(Z=z)) for all z in Z.
                Dtarg = 1 # The target dim.
                Dfilt = len(filtSpecs)  # Filterning Dom
                Dcond = len(condSpecs)  # Conditionalize Dim
                Dquery = Dtarg + Dfilt + Dcond # Query Dim
                Nfilt = self.N**((Dtarg + Dcond)/Dquery) # Number of points to return from filter
                # First, we filter on the bound conditions (if any), then conditionalize on the reduced set
                if filtSpecs:
                    minP_Filt = .8 * Nfilt
                    maxP_Filt = 1.2 * Nfilt
                    ss = self.SubSpace(filtSpecs, minPoints=minP_Filt, maxPoints=maxP_Filt, discSpecs=self.discSpecs, fixDistr=True)
                    #print('ss.N, min, max = ', ss.N, minP_Filt, maxP_Filt)
                else:
                    ss = self
                Ntarg = ss.N**(Dtarg/(Dcond + Dtarg)) # Number of points to return from final
                minP = .8 * Ntarg
                maxP = 1.2 * Ntarg
                condFiltSpecs = self.getCondSpecs(condSpecs, power=power, effN=ss.N)
                accum = np.zeros((bins,))
                allProbs = 0.0 # The fraction of the probability space that has been tested.
                allPoints = 0
                for cf in condFiltSpecs:
                    # Create a new subspace filtered by both the bound and unbound conditions
                    # Note that progressive filtering will be used for the unbound conditions.
                    # probYgZ is P(Y | Z=z) e.g., P(Y | X=1, Z=z)
                    ss2 = ss.SubSpace(cf, minPoints=minP, maxPoints=maxP, discSpecs=ss.discSpecs, fixDistr=True)
                    #print('ss2.N, min, max = ', ss2.N, minP, maxP)
                    if ss2.N < 1:
                        continue
                    #print('ss2.N = ', ss2.N)

                    probYgZ = ss2.distr(rvName)
                    #probYgZ = filtSpace.distr(rvName, cf)
                    # Now we can compute probZ as ratio of the number of data points in the filtered distribution and the original
                    #print('parentQuery = ', ss2.parentQuery)
                    probZ = self.P(ss2.parentQuery)
                    #print('probZ = ', probZ, ', probYgZ.E() = ', probYgZ.E(), ', probYgZ.N = ', probYgZ.N, ', ss.N = ', ss.N, ', ss.query = ', ss.parentQuery, ', ss2.query = ', ss2.parentQuery)
                    if probZ == 0:
                        # Zero probability -- don't bother accumulating
                        continue
                    probs = probYgZ.ToHistogram() * probZ # Creates an array of probabilities
                    accum += probs
                    allProbs += probZ
                    allPoints += ss2.N
                if allProbs > 0:
                    accum = accum / allProbs
                    # Now we start with a pdf of the original variable to establish the ranges, and
                    # then replace the actual probabilities of each bin.  That way we maintain the
                    # original bin structure. 
                    template = self.distr(rvName)
                    outSpecs = []
                    for i in range(len(accum)):
                        pdfBin = template.getBin(i)
                        newprob = accum[i]
                        newBin = pdfBin[:-1] + (newprob,)
                        outSpecs.append(newBin)
                    outPDF = PDF(allPoints, outSpecs, isDiscrete = isDiscrete, rvName=rvName)
                else:
                    outPDF = None
        self.distrCache[cacheKey] = outPDF
        return outPDF

    PD = distr

    # Return (targIsDiscrete, discConds, contConds)
    def analyzeQuery(self, rvName, condSpecs):
        targIsDisc = self.isDiscrete(rvName) # Is Target Discrete
        discConds = [] # Discrete conditions
        contConds = [] # Continuous conditions
        for condSpec in condSpecs:
            varName = condSpec[0]

            if self.isDiscrete(varName):
                discConds.append(condSpec)
            else:
                contConds.append(condSpec)
        return (targIsDisc, discConds, contConds)
        

    def boundCondition(self, rvName, condSpecs):
        #print('***** Bound Condition')
        targIsDisc, discConds, contConds = self.analyzeQuery(rvName, condSpecs)
        #('***** Bound Condition: ', targIsDisc, discConds, contConds)
        
        if targIsDisc:
            # Use only filtering if target is discrete.  We need a distribution for the
            # target in order to analyze.  

            filtConds = condSpecs
            contConds = []
        else:
            filtConds = discConds

        if filtConds:
            # We have variables that need filtering (either due to discrete conditionals, or discrete target).
            # Create a new subspace with the filter variables removed.
            Dtarg = len(contConds) + 1 # Dimension of continous conditions plus 1 for the target.
            Dcond = len(filtConds)  # Number of dimensions to filter
            Dquery = Dtarg + Dcond # Dim of the query
            Ntarg = self.N**(Dtarg/Dquery) # Number of points to return from filter
            #filtSpace = self.SubSpace(filtConds, density = self.density, power = self.power, discSpecs=self.discSpecs, minPoints = Ntarg * .8, maxPoints = Ntarg * 1.2)
            filtSpace = self.SubSpace(filtConds, density = self.density, power = self.power, minPoints = Ntarg * .8, maxPoints = Ntarg * 1.2)
        else:
            filtSpace = self  # No required pre-filtering

        #filtSpace = self.SubSpace(condSpecs, density = self.density, power = self.power, minPoints = 100, maxPoints = sqrt(self.N))
        if contConds:
            # We still have continuous variables to process. Use U-Prob(L)
            # Set L, the Lambda value for U-Prob
            if self.cMethod == 'd':
                L = 100
            elif self.cMethod == 'j':
                L = 0
            else:
                # Let U-Prob utomatically determine setting for L
                L = None
            if self.cMethod != 'd!':  # Anything but old stle discretiza\ation
                # Use the filtered space remaining after removing discrete vars.
                upr = uprob.UPROB(filtSpace, rvName, condSpecs=contConds, lmbda=L)
                outPDF = upr.distr()
            else:
                # d! -- forced discretization -- old style
                Dtarg = 1 # The target dim.
                Dcond = len(contConds)  # Conditional Dim
                Dquery = Dtarg + Dcond # Query Dim
                Ntarg = filtSpace.N**(Dtarg/Dquery) # Number of points to return from filter
                filtSpace = filtSpace.SubSpace(contConds, density = self.density, power = self.power, discSpecs=self.discSpecs, minPoints = Ntarg * .8, maxPoints = Ntarg * 1.2)
                if filtSpace.N > 0:
                    #print('filtspace.N, parentQuery = ', filtSpace.N, filtSpace.parentQuery)
                    outPDF = filtSpace.distr(rvName)
                else:
                    outPDF = None
        else:
            # We have already processed all conditions.  We have the final distribution.
            pass

            if filtSpace.N > 0:
                #print('filtspace.N, parentQuery = ', filtSpace.N, filtSpace.parentQuery)
                outPDF = filtSpace.distr(rvName)
            else:
                outPDF = None
        #print('outPdf: ', outPDF.N, outPDF.E(), outPDF.percentile(2.5), outPDF.percentile(97.5))
        return outPDF

    def reductionExponent(self, totalDepth):
        minPoints = 200
        p = log(minPoints, self.N)**(1 / totalDepth)
        return p

    def getCondSpecs(self, condSpecs, power, effN=None):
        """ Produce a set of conditional specifications for stochastic
            conditionalization, given
            a set of variables to conditionalize on, and a desired power level.
            Power determines how many points to use to conditionalize on.
            Zero indicates conditionalize on the mean alone. 1 uses the mean
            and two other points (one on either side of the mean).
            2 Uses the mean plus 4 other points (2 on each side of the mean).
            Power values (p) less than 100 will test p * 2 + 1 values for each
            variable.range
            power value > 100 indicates that all values will be tested,
            which can be extremely processor intensive.
            Conditional specifications provide a list of lists of tuple:
            [[(varName1, value1_1), (varName2, value2_1), ... (varNameK, valueK_1)],
             [(varName1, value1_2), (varName2, value2_2), ... (varNameK, valueK_2)],
             ...
             [(varName1, value1_N), (varName2, value2_N), ... (varNameK, valueK_N)]]
            Where K is the number of conditional variables, and N is the total number
            of combinations = K**(2 * P + 1) for values of P < 100.
        """
        if effN is None:
            effN = self.N
        if effN > 2:
            delta = .3 / log(effN, 10)
        else:
            delta = .3
        if DEBUG:
            print('ProbSpace.getCondSpecs: delta = ', delta, ', effN = ', effN)
        #print('getCondSpecs: delta = ', delta, ', effN = ', effN)
        condVars = [spec[0] for spec in condSpecs]
        testValList = self.getCondSpecs2(condVars, power = power)
        # Get the minimum and maximum raw values for each variable

        #print('rawCS = ', rawCS)
        outCS = []
        stats = {}
        # Prepopulate stats for each variable (mean, std)
        for var in condVars:
            distr = self.distr(var)
            mean = distr.E()
            std = distr.stDev()
            minv = distr.minVal()
            maxv = distr.maxVal()
            stats[var] = (mean, std, minv, maxv)
        
        def generateIndexCombos(testValList):
            if len(testValList) == 1:
                return [(indx,) for indx in range(len(testValList[0]))]
            else:
                outCombos = []
                childTestVals = generateIndexCombos(testValList[1:])
                testVals0 = testValList[0]
                for i in range(len(testVals0)):
                    outCombo = [(i,) + childTestVals[j] for j in range(len(childTestVals))]
                    outCombos += outCombo
                return outCombos
        
        combos = generateIndexCombos(testValList)
        #print('combos = ', combos)
        # Scale and center the raw test points.
        testCounts = [len(testValList[i]) for i in range(len(testValList))]
        for i in range(len(combos)):
            indexes = combos[i]
            outSpec = []
            # Adjust pseudo filters by the mean and std of the conditional
            for s in range(len(indexes)):
                indx = indexes[s]
                var = condVars[s]
                val = testValList[s][indx]
                if self.isDiscrete(var):
                    if self.isCategorical(var) or self.cardinality(var) == 2:
                        # Take the values verbatim for categorical vars
                        outSpec.append((var, val))
                    else:
                        # For discrete numeric vars, check the range betweeen values
                        if indx == 0:
                            # Take the first value from -inf to the next value
                            outSpec.append((var, None, val))
                        else:
                            # Take the interval [spec[i-1], spec[i])
                            prev = testValList[s][indx-1]
                            if indx == len(testValList[s]) - 1:
                                # If it's the last one, then include out to +inf
                                outSpec.append((var, prev, None))
                            else:
                                outSpec.append((var, prev, val))
                           
                else:
                    # Continuous.  Create small ranges based on delta
                    mean, std, minv, maxv = stats[var]
                    delta = min(.3, 6 / (power*2+1))
                    # Mean + val +/- delta
                    varSpec = (var, mean + (val - delta) * std, mean + (val + delta) * std)
                    #print('varSpec = ', varSpec, ', val = ', val, ', mean, std = ', mean, std)
                    outSpec.append(varSpec)
            outCS.append(outSpec)
        #print('outCS = ', outCS)
        return outCS
        # End of getCondSpecs

    def getCondSpecs2(self, condVars, power=2):
        """
        Generate a set of unscaled test values.  These are in terms of standard deviations
        from the mean.
        """
        def getTestVals(self, rvName, levelSpecs):
            testVals = []
            isDiscrete = self.isDiscrete(rvName)
            if isDiscrete:
                # If catagorical, return all values
                # If discrete, return some discrete samples
                # If power >= 100, return all bins.
                if self.isCategorical(rvName) or power >= 100:
                    testVals = self.getMidpoints(rvName)
                else:
                    # Discrete numeric.  Sample a range of values
                    allVals = self.getMidpoints(rvName)
                    nSamples = int(2 * power + 1)
                    nVals = len(allVals)
                    if nVals <= nSamples:
                        testVals = allVals
                    else:
                        # Let's try and reduce the number of values.
                        reduction = int(nVals / nSamples)
                        # Take every Kth value, where K = reduction
                        sampleIndxs = range(0, nVals, reduction)
                        # Since the above sometimes loses the last value (when nVals is even),
                        # we'll take the center nSamples out of the remaining values, but 
                        # when the remaining values - nSamples is odd, we'll favor the later values.
                        start = ceil((len(sampleIndxs) - nSamples) / 2)
                        # Extract the nSamples center values.
                        try:
                            sampleIndxs = sampleIndxs[start : start + nSamples]
                        except:
                            assert False, 'Get test values (start, nSamples, power, rvName)' + str((start, nSamples, power, rvName))
                        testVals = [allVals[indx] for indx in sampleIndxs]                        
            else:
                # If continuous, sample values at various distances from the mean
                for tp in levelSpecs:
                    if tp == 0:
                        # For 0, just use the mean
                        testVals.append(0)
                    else:
                        # For nonzero, test points mean + tp and mean - tp
                        testVals.append(-tp,)
                        testVals.append(tp,)
            return testVals # End getTestVals

        if power == 0:
            maxLevel = .5
            levelSpecs = [0]
        else:
            maxLevel = .5 + log(power, 10)
            #print('maxLevel = ', maxLevel)
            levelSpecs = [0] + list(np.arange(1/power*maxLevel, maxLevel + 1/power*maxLevel, 1/power*maxLevel))
        #print('levelSpecs = ', levelSpecs)
        testValList = []
        # Find values for each variable based on testPoints
        nVars = len(condVars)
        for rvName in condVars:
            # Only one var to do.  Find the values.
            testVals = getTestVals(self, rvName, levelSpecs)
            testValList.append(testVals)
        #print('testValList = ',  testValList)
        return testValList
        # End of getCondSpecs2
        
    def testDirection(self, rvA, rvB, givenSpecs=[], power=None, N_train=2000):
        """ When having power parameter less than or equal to 1,
            test the causal direction between variables A and B
            using one of the LiNGAM or GeNGAM pairwise algorithms.

            When having power larger than 1, use non-linear method
            to test the causal direction. N_train determines at most
            how many samples would be used to train the non-linear
            model. Currently test uses KNN algorithm.

            Returns a number R.  A positive R indicates that the
            causal path runs from A toward B.  A negative value
            indicates a causal path from B towards A.  Values
            close to zero (e.g. +/- 10**-5) means that causal
            direction could not be determined.
        """
        if power is None:
            power = self.power
        givenSpecs = self.normalizeSpecs(givenSpecs)
        cacheKey = (rvA, rvB, tuple(givenSpecs), power)
        if cacheKey in self.dirCache:
            rho = self.dirCache[cacheKey]
        else:
            if givenSpecs:
                # There are conditions.  Create a subspace based on the conditions
                # and do testDirecton on that subspace (with no conditions).
                ss = self.SubSpace(givenSpecs)
                if ss.N < 100:
                    rho = 0.0
                else:
                    rho = ss.testDirection(rvA, rvB, power=power, N_train=N_train)
            else:
                # Unconditional.
                # Standardize the data.
                if self.cardinality(rvA) > 2 and not self.isCategorical(rvA):
                    standA = self.standCache.get(rvA, None)
                    if standA is None:
                        standA = standardize(self.ds[rvA])
                        self.standCache[rvA] = standA
                else:
                    standA = self.ds[rvA]
                if self.cardinality(rvB) > 2 and not self.isCategorical(rvB):
                    standB = self.standCache.get(rvB, None)
                    if standB is None:
                        standB = standardize(self.ds[rvB])
                        self.standCache[rvB] = standB
                else:
                    standB = self.ds[rvB]
                # Call the direction module with standardized data.
                # Use UC model for categorical and binary data.
                if (self.isCategorical(rvA) or (self.isDiscrete(rvA) and self.cardinality(rvA) < 3)) \
                        and (self.isCategorical(rvB) or (self.isDiscrete(rvB) and self.cardinality(rvB) < 3)):
                    if rvA in self.stringMapR.keys() and rvB in self.stringMapR.keys():
                        # For readability with testing
                        rho, identifiable = ucm.uniform_channel_test(standA, standB, AMap=self.stringMapR[rvA], BMap=self.stringMapR[rvB])
                    else:
                        rho, identifiable = ucm.uniform_channel_test(standA, standB)
                else:
                    # Use ANM test
                    rho = direction.test_direction(standA, standB, power, N_train)

        # Add result to cache
            self.dirCache[cacheKey] = rho
            # Add reverse result to cache, with reversed rho
            reverseKey = (rvB, rvA, tuple(givenSpecs), power)
            self.dirCache[reverseKey] = -rho
        return rho

    def adjustSpec(self, spec, delta):
        outSpec = []
        for var in spec:
            if len(var) == 2:
                # Discrete.  Don't modify
                outSpec.append(var)
            else:
                varName, low, high = var
                mid = (low + high) / 2.0
                oldDelta = (high - low) / 2.0
                newDelta = delta * oldDelta
                newVar = (varName, mid - newDelta, mid + newDelta)
                #print('oldVar = ', var, 'newVar = ', newVar, mid, oldDelta, newDelta, deltaAdjust)
                outSpec.append(newVar)
        #print('old = ', spec, ', new =', outSpec, ', delta = ', deltaAdjust)
        return outSpec

    def dependence(self, rv1, rv2, givenSpecs=[], power=None, raw=False, seed=None, num_f=100, num_f2=5, sensitivity=None,
                   dMethod='rcot'):
        """
        givens is [given1, given2, ... , givenN]

        This function include two different method 'prob' and 'rcot' to test dependence.
        Parameter power is for 'prob' method.
        Parameter seed is for 'rcot' method to determine the random seed. The same seed will return same results
        on the same dataset.
        Parameter num_f is the number of features for conditioning set, num_f2 is the number of features for
        non-conditioning set in 'rcot' method.
        """
        if power is None:
            power = self.power
        cacheKey = (rv1, rv2, tuple(givenSpecs), power, sensitivity)
        if cacheKey in self.dependCache:
            return self.dependCache[cacheKey]
        if power == 0 and not givenSpecs:
            # For unconditional with power = 0, return the absolute correlation coef
            dep = abs(self.corrCoef(rv1, rv2))
            self.dependCache[cacheKey] = dep
            return dep
        if sensitivity is None:
            sensitivity = 10
        if dMethod == "rcot":
            givenSpecs = self.normalizeSpecs(givenSpecs)
            givensU, givensB = self.separateSpecs(givenSpecs)

            if givensB:
                ss1 = self.getCondSpace(givensB, Dtarg=2)
            else:
                ss1 = self
            x = np.array(ss1.ds[rv1])
            y = np.array(ss1.ds[rv2])
            if power < 100:
                maxRecs = min([int(10000 * power), ss1.N])
            else:
                maxRecs = ss1.N
            #maxRecs = ss1.N
            

            if not givensU:
                try:
                    (p, Sta) = RCoT(x, y, num_f=num_f, num_f2=num_f2, seed=seed)
                except:
                    if DEBUG:
                        print('ProbSpace.dependence: RCoT Failed.  Using discrete method:', rv1, rv2)
                    return self.dependence(rv1, rv2, givenSpecs, power=power,dMethod='d')
            else:
                z = []
                for rv in givensU:
                    z.append(np.array(ss1.ds[rv[0]]))
                if maxRecs < ss1.N:
                    inds = np.random.choice(ss1.N, size=maxRecs, replace=False)
                    x = x[inds]
                    y = y[inds]
                    for d in range(len(z)):
                        z[d] = z[d][inds]
                try:
                    (Cxy_z, Sta, p) = RCoT(x, y, z, num_f=num_f, num_f2=num_f2, seed=seed)
                except:
                    if DEBUG:
                        print('ProbSpace.dependence: RCoT Failed.  Using discrete method:', rv1, rv2, givenSpecs)
                    return self.dependence(rv1, rv2, givenSpecs, power=power,dMethod='d')
            #assert 1 <= sensitivity <= 10, "sensitivity should be from range [1, 10]"
            if sensitivity >= 10:
                # Use 0.99 as threshold to determine whether a pair of variables are dependent
                dep = (1 - p[0]) ** log(0.5, 0.99)
            else:
                staScaled = log(Sta / (num_f2**2) + 1)
                #print('staScaled = ', staScaled)
                threshold = 11 - sensitivity
                if staScaled <= threshold:
                    dep =  0.5 - math.tanh(threshold - staScaled) / 2
                else:
                    dep =  0.5 + math.tanh(staScaled - threshold) / 2
            self.dependCache[cacheKey] = dep
            return dep
        # Get all the combinations of rv1, rv2, and any givens
        # Depending on power, we test more combinations.  If level >= 100, we test all combos
        # For level = 0, we just test the mean.  For 1, we test the mean + 2 more values.
        # For level = 3, we test the mean + 6 more values.

        # Separate the givens into bound (e.g. B=1, 1 <= B < 2) and unbound (e.g., B) specifications.
        givensU, givensB = self.separateSpecs(givenSpecs)
        if not givensU:
            condFiltSpecs = [None]
        else:
            condFiltSpecs = self.getCondSpecs(givensU, power=power)
        accum = 0.0
        accumProb = 0.0
        prevGivens = None
        prevProb1 = None
        numTests = 0
        for spec in condFiltSpecs:
            # Compare P(Y | Z) with P(Y | X,Z)
            # givens is conditional on spec without rv2
            #print('spec = ', spec)
            if spec is None: # Unconditional Independence
                Dtarg = 2
                if givensB:
                    ss1 = self.getCondSpace(givensB, Dtarg=Dtarg)
                else:
                    ss1 = self
                prob1 = ss1.distr(rv1)
            else:
                givens = spec
                if givens != prevGivens:
                    # Only recompute prob 1 when givensValues change
                    # Get a subsapce filtered by all givens, but not rv2
                    Dtarg = 2
                    ss1 = self.getCondSpace(givens + givensB, Dtarg=Dtarg)
                    if ss1.N == 0:
                        continue
                    prob1 = ss1.distr(rv1)
                    prevProb1 = prob1
                    prevGivens = givens
                else:
                    # Otherwise use the previously computed prob1
                    prob1 = prevProb1
            if prob1.N <= 1:
                #print('Empty distribution: ', spec)
                continue
            #print('ss1.N = ', ss1.N)
            testSpecs = ss1.getCondSpecs([(rv2,)], power)
            #print('rv, testSpecs, power = ', rv2, testSpecs, power)
            for testSpec in testSpecs:
                # prob2 is the conditional subspace of everything but rv2
                # conditioned on rv2
                Dtarg = 1
                #print('testSpec = ', testSpec)
                ss2 = ss1.getCondSpace(testSpec, Dtarg=Dtarg)
                if ss2.N == 0:
                    continue
                prob2 = ss2.distr(rv1)
                if prob2.N == 0:
                    continue
                dep = prob1.compare(prob2, raw=raw)
                # We accumulate any measured dependency multiplied by the probability of the conditional
                # clause.  This way, we weight the dependency by the frequency of the event.
                condProb = prob2.N / self.N
                accum += dep * condProb
                accumProb += condProb # The total probability space assessed
                numTests += 1
                if DEBUG:
                    print('spec = ', spec, testSpec, ', givensB = ', givensB, ', dep = ', dep, ', prob1.N, prob2.N = ', prob1.N, prob2.N)
                    print('ss1.parentQuery = ', ss1.parentQuery, ', ss2.parentQuery = ', ss2.parentQuery)
            #print('ss1.N, ss2.N = ', ss1.N, ss2.N)
        if accumProb > 0.0:
            # Normalize the results for the probability space sampled by dividing by accumProb
            dependence = accum / accumProb
            if not raw:
                # Bound it to [0, 1] 
                dependence = max([min([dependence, 1]), 0])
            dep =  dependence
            #H = .36845
            #L = .0272
            #if dependence < L:
            #    calDep = dependence / (2*L)
            #else:
            #    calDep = (dependence - L) / (H-L) / 2 + .5
            # Bound it to [0, 1] 
            #calDep = max([min([calDep, 1]), 0])
            #return calDep
        else:
            #print('Cond distr too small: ', rv1, rv2, givenSpecs)
            dep =  0.0
        self.dependCache[cacheKey] = dep
        return dep

    def separateSpecs(self, specs):
        """ Separate bound and unbound variable specs,
            and return (unboundSpecs, boundSpecs).  
        """
        delta = .05
        uSpecs = []
        bSpecs = []
        for spec in specs:
            if type(spec) == type((0,)) and len(spec) > 1:
                # It is a bound spec
                bSpecs.append(spec)
            else:
                # Unbound
                uSpecs.append(spec)
        return uSpecs, bSpecs



    def independence(self, rv1, rv2, givenSpecs=[], power=None, seed=None, num_f=100, num_f2=5, sensitivity=5,
                     dMethod='rcot'):
        """
            Calculate the independence between two variables, and an optional set of givens.
            This is a heuristic inversion
            of the dependence calculation to match other independence measures which return
            the likelihood of the null hypothesis that the variables are dependent.
            A threshold of .1 is generally used.  Values below that are considered dependent.
            givens are formatted the same as for prob(...).
            TO DO: Calibrate to an exact p-value.
        """
        dep = self.dependence(rv1, rv2, givenSpecs=givenSpecs, power=power, seed=seed, num_f=num_f, num_f2=num_f2,
                              sensitivity=sensitivity, dMethod=dMethod)
        ind = 1 - dep
        return ind


    def isIndependent(self, rv1, rv2, givenSpecs=[], power=None, seed=None, num_f=100, num_f2=5, sensitivity=5,
                      dMethod='rcot'):
        """ Determines if two variables are independent, optionally given a set of givens.
            Returns True if independent, otherwise False
        """
        ind = self.independence(rv1, rv2, givenSpecs = givenSpecs, power = power, seed=seed, num_f=num_f, num_f2=num_f2,
                              sensitivity=sensitivity, dMethod=dMethod)
        # Use .5 (50% confidence as threshold.
        return ind > .5

    def jointProb(self, varSpecs, givenSpecs=None):
        """ Return the joint probability given a set of variables and their
            values.  varSpecs is of the form (varName, varVal).  We want
            to find the probability of all of the named variables having
            the designated value.
        """

        if givenSpecs is None:
            givenSpecs = []
        Dtarg = len(varSpecs) # The target dim.
        Dcond = len(givenSpecs) # The conditional dimension
        Dquery = Dtarg + Dcond # The query dimension
        if Dcond > 0:
            Ntarg = self.N**(Dtarg / Dquery)
            minP = .8 * Ntarg
            maxP = 1.2 * Ntarg
            ss = self.SubSpace(givenSpecs, minPoints=minP, maxPoints=maxP)
        else:
            ss = self
        Ntarg = ss.N**(.5)
        minP = .8 * Ntarg
        maxP = 1.2 * Ntarg
        ss2 = ss.SubSpace(varSpecs, minP, maxP)
        if ss2.N > 0:
            jp = ss2.N / ss.N
        else:
            jp = 0
        return jp

    def corrCoef(self, rv1, rv2):
        """Pearson Correlation Coefficient (rho)
        """
        indx1 = self.fieldIndex[rv1]
        indx2 = self.fieldIndex[rv2]
        dat1 = self.aData[indx1,:]
        dat2 = self.aData[indx2,:]
        mean1 = dat1.mean()
        mean2 = dat2.mean()
        num1 = 0.0
        denom1 = 0.0
        denom2 = 0.0
        for i in range(self.N):
            v1 = dat1[i]
            v2 = dat2[i]
            diff1 = v1 - mean1
            diff2 = v2 - mean2
            num1 += diff1 * diff2
            denom1 += diff1**2
            denom2 += diff2**2
        rho = num1 / (denom1**.5 * denom2**.5)
        return rho

    def Predict(self, Y, X, useVars=None, cMethod='d!'):
        """
            Y is a single variable name.  X is a dataset
        """
        if cMethod[0] != 'd':
            xVars = list(X.keys())
            nTests = len(X[xVars[0]])
            results = []
            for i in range(nTests):
                conds = []
                for var in xVars:
                    conds.append((var, X[var][i]))
                result = self.E(Y, conds, cMethod='j', smoothness=.25)
                results.append(result)
            return results
        else:
            dists = self.PredictDist(Y, X, useVars)
            preds = [dist.E() for dist in dists]
            return preds

    def Classify(self, Y, X, useVars=None):
        """
            Y is a single variable name.  X is a dataset.
        """
        assert self.isDiscrete(Y), 'Prob.Classify: Target variable must be discrete.'
        dists = self.PredictDist(Y, X, useVars)
        preds = [dist.mode() for dist in dists]
        return preds

    def PredictDist(self, Y, X, useVars=None):
        """
            Y is a single variable name.  X is a dataset.
        """
        if DEBUG:
            print('ProbSpace.PredictDist: Y, Xvars, useVars = ', Y, X.keys(), useVars)
        outPreds = []
        # Make sure Y is not in X
        if useVars is not None:
            # Use the independent variables as specified
            vars = useVars
        else:
            # Calculate the variables to use.  No point
            # in using variables that are independent of the target,
            # so we filter those out.  We also make sure the target is
            # not in the variable list.
            vars = list(X.keys())
            # Make sure the target is not in the variable list.
            try:
                vars.remove(Y)
            except:
                pass
            # Sort the independent variables by dependence with Y
            deps = [(self.dependence(var, Y, power=3), var) for var in vars]
            deps.sort()
            deps.reverse()
            vars = []
            # Remove any independent independents
            for i in range(len(deps)):
                dep = deps[i]
                if dep[0] < .5:
                #if dep[0] < 0: # Temp disable
                    print('Prob.PredictDist: rejecting variables due to independence from target(p-value, var): ', deps[i:])
                    break
                else:
                    vars.append(dep[1])
            #print('vars = ', vars)
            # Vars now contains all the variables that are not indpendent from Y, sorted in
            # order of highest dependence.            

        # Get the number of items to predict:
        numTests = len(X[vars[0]])
        targetIsDiscrete = self.isDiscrete(Y)
        for i in range(numTests):
            filts = []
            for var in vars:
                val = X[var][i]
                filts.append((var, val))
            maxPoints = min([sqrt(self.N), 200])
            fs = self.SubSpace(filts, minPoints=10, maxPoints=maxPoints)
            #fs = self.SubSpace(filts)
            if DEBUG and fs.N > maxPoints and not targetIsDiscrete:
                print('subspace.N = ', fs.N)
            if fs.N == 0:
                if DEBUG:
                    print('no examples found.')
                pred = self.distr(Y) # Use the natural target distribution
            else:
                pred = fs.distr(Y)
            outPreds.append(pred)
        return outPreds

    def Plot(self):
        """ Plot the distribution of each variable in the joint probability space
            using matplotlib.
        """
        inf = 10**30
        plotDict = {}
        minX = inf
        maxX = -inf
        numPts = 200
        pdfs = []
        for v in self.fieldList:
            d = self.distr(v)
            minval = d.percentile(5)
            maxval = d.percentile(95)
            if maxval > maxX:
                maxX = maxval
            if minval < minX:
                minX = minval
            pdfs.append(d)
        xvals = []
        for i in range(numPts):
            rangex = maxX - minX
            incrx = rangex / numPts
            xvals.append(minX + i * incrx)
        for i in range(len(self.fieldList)):
            yvals = []
            var = self.fieldList[i]
            pdf = pdfs[i]
            for j in range(numPts):
                xval = xvals[j]
                if j == numPts - 1:
                    P = pdf.P((xval, maxX))
                else:
                    P = pdf.P((xval, xvals[j+1]))
                yvals.append(P)
            plotDict[var] = yvals
        plotDict['_x_'] = xvals
        probCharts.plot(plotDict)

