'''Add additional scattering to processed photons.

Classes in this file add additional scattering in a statistical sense.

The classes in this module do not trace the to a specific location, they just add
the scatter at the point of the last interaction.
For example, reflection from a (flat) mirror is implemented as a perfect reflection, but in
practice there is some roughness to the mirror that adds a small Gaussian blur to the
reflection. To represent this, the point of origin of the ray remains unchanged, but a small
random change is added to the direction vector.
'''
import numpy as np

from ..math.pluecker import e2h, h2e
from ..math.rotations import axangle2mat
from .base import OpticalElement

class RadialMirrorScatter(OpticalElement):
    '''Add scatter to any sort of radial mirror.

    Scatter is added in the plane of reflection, which is defined here
    as the plane which contains (i) the current direction the ray and (ii) the
    vector connecting the center of the `RadialMirrorScatter` element and the
    point of last interaction for the ray.
    Scatter can also be added perpendicular to the plane-of-reflection.

    Parameters
    ----------
    inplanescatter : float
        sigma of Gaussian for in-plane scatter [in radian]
    perpplanescatter : float
        sigma of Gaussian for scatter perpendicular to the plane of reflection
        [in radian] (default = 0)
    '''
    def __init__(self, **kwargs):
        self.inplanescatter = kwargs.pop('inplanescatter') # in rad
        self.perpplanescatter = kwargs.pop('perpplanescatter', 0.) # in rad
        super(RadialMirrorScatter, self).__init__(**kwargs)

    def process_photons(self, photons):
        # change this line, if you want to process only some photons (intersect et al.)
        n = len(photons)
        center = self.pos4d[:-1, -1]
        radial = h2e(photons['pos'].data) - center
        perpplane = np.cross(h2e(photons['dir'].data), radial)
        inplaneangle = np.random.normal(loc=0., scale=self.inplanescatter, size=n)

        rot = axangle2mat(perpplane, inplaneangle)
        photons['dir'] = e2h(np.einsum('...ij,...i->...j', rot, h2e(photons['dir'])), 0)

        if self.perpplanescatter !=0: # Works for 0 too, but waste of time to run
            perpangle = np.random.normal(loc=0., scale=self.perpplanescatter, size=n)
            rot = axangle2mat(radial, perpangle)
            photons['dir'] = e2h(np.einsum('...ij,...i->...j', rot, h2e(photons['dir'])), 0)

        return photons
