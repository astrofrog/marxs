'''Tools for setting up instruments in the Rowland Torus geometry.'''

from __future__ import division

import numpy as np
from scipy import optimize
import transforms3d
from transforms3d.utils import normalized_vector

from ..optics.base import OpticalElement
from ..base import _parse_position_keywords, MarxsElement
from ..optics import FlatDetector
from ..math.rotations import ex2vec_fix
from ..math.pluecker import e2h, h2e
from ..math.utils import anglediff
from ..simulator import Parallel
from ..visualization.utils import get_color

def find_radius_of_photon_shell(photons, mirror_shell, x, percentile=[1,99]):
    '''Find the radius the photons coming from a single mirror shell have.

    For nested Wolter Type I mirrors the ray of photons reflected from a single mirror
    shell essentially form a cone in space. The tip of the cone is at the focal point
    and the base is at the mirror. There is a certain thickness to this cone depending
    on where exactly on the mirror the individual photon was reflected.

    This function takes a photon list of photons after passing through the mirror and
    calculates the radius range that this photon cone covers at a specific distance from
    the focal point. This information can help to design the placement of gratings.

    Parameters
    ----------
    photons : `~astropy.table.Table`
        Photon list with position and direction of photons leaving the mirror
    mirror_shell : int
        Select mirror shell to look at (uses column ``mirror_shell`` in ``photons``
        for filtering).
    x : float
        Distance along the optical axis (assumed to coincide with the x axis with focal point
        at 0).
    percentile : list of floats
        The radius is calculated at the given percentiles. ``50`` would give the median radius.
        The default of ``[1, 99]`` gives a radius range excluding extrem outliers such as
        stray rays scattered into the extreme wing of the PSF.
    '''
    p = photons[:]
    mdet = FlatDetector(position=np.array([x, 0, 0]), zoom=1e8, pixsize=1.)
    p = mdet.process_photons(p)
    ind = (p['probability'] > 0) & (p['mirror_shell'] == mirror_shell)
    r = np.sqrt(p['det_x'][ind]**2+p['det_y'][ind]**2.)
    return np.percentile(r, percentile)


class RowlandTorus(MarxsElement):
    '''Torus with y axis as symmetry axis.

    Note that the origin of the torus is the focal point, which is **may or may not**
    be the same as the center of the torus.

    Parameters
    ----------
    R : float
        Radius of Rowland torus. ``r`` determines the radius of the Rowland circle,
        ``R`` is then used to rotate that circle around the axis of symmetry of the torus.
    r : float
        Radius of Rowland circle
    '''

    display = {'color': (1., 0.3, 0.3),
               'opacity': 0.2}


    def __init__(self, R, r, **kwargs):
        self.R = R
        self.r = r
        self.pos4d = _parse_position_keywords(kwargs)
        super(RowlandTorus, self).__init__(**kwargs)

    def quartic(self, xyz, transform=True):
        '''Quartic torus equation.

        Roots of this equation are points on the torus.

        Parameters
        ----------
        xyz : np.array of shape (N, 3) or (3)
            Coordinates of points in euklidean space. The quartic is calculated for
            those points.
        transform : bool
            If ``True`` transform ``xyz`` from the global coordinate system into the
            local coordinate system of the torus. If this transformation is done in the
            calling function already, set to ``False``.

        Returns
        -------
        q : np.array of shape (N) or scalar
            Quartic at the input location
        '''
        if xyz.shape[-1] != 3:
            raise ValueError('Input coordinates must be defined in Eukledian space.')

        if transform:
            invpos4d = np.linalg.inv(self.pos4d)
            xyz = h2e(np.einsum('...ij,...j', invpos4d, e2h(xyz, 1)))
        return ((xyz**2).sum(axis=-1) + self.R**2. - self.r**2.)**2. - 4. * self.R**2. * (xyz[..., [0,2]]**2).sum(axis=-1)

    def solve_quartic(self, x=None, y=None, z=None, interval=[0, 1], transform=True):
        '''Solve the quartic for points on the Rowland torus in Cartesian coordinates.

        This method solves the quartic equation for positions on the Rowland Torus for
        cases where two of the Cartesian coordinates are fixed (e.g. y and z) and the third
        one (e.g. x) needs to be computed. This function is intended as a convenience for a
        common use case. In more general cases, evaluate the :meth:`RowlandTorus.quartic` and
        search for the roots of that function.

        Parameters
        ----------
        x, y, z : float or None
            Set two of these coordinates to fixed numbers. This method will solve for the
            coordinate set to ``None``.
            x, y, z are defined in the global coordinate system.
        interval : np.array
            [min, max] for the search. The quartic can have up to for solutions because a
            line can intersect a torus in four points and this interval must bracket one and only
            one solution.
        transform : bool
            If ``True`` transform ``xyz`` from the global coordinate system into the
            local coordinate system of the torus. If this transformation is done in the
            calling function already, set to ``False``.

        Returns
        -------
        coo : float
            Value of the fitted coordinate.
        '''
        n_Nones = 0
        for i, c in enumerate([x, y, z]):
            if c is None:
                n_Nones +=1
                ind = i
        if n_Nones != 1:
            raise ValueError('Exactly one of the input numbers for x,y,z must be None.')
        # Need to give it a number for vstack to work
        if ind == 0: x = 0.
        if ind == 1: y = 0.
        if ind == 2: z = 0.

        xyz = np.vstack([x,y,z]).T
        def f(val_in):
            xyz[..., ind] = val_in
            return self.quartic(xyz, transform=transform)
        val_out, brent_out = optimize.brentq(f, interval[0], interval[1], full_output=True)
        if not brent_out.converged:
            raise Exception('Intersection with torus not found.')
        return val_out

    def parametric(self, theta, phi):
        '''Parametric description of the torus.

        This is just another way to obtain the shape of the torus, e.g.
        for visualization.

        Parameters
        ----------
        theta : np.array
            Points on the Rowland circle (specified in rad, where 0 is on the
            optical axis across from the focal point).
        phi : np.array
            ``phi`` rotates the Rowland circle to make up the Rowland torus.

        Returns
        -------
        xyzw : np.array
            Torus coordinates in global homogeneous coordinate system.
        '''
        x = (self.R + self.r * np.cos(theta)) * np.cos(phi)
        z = (self.R + self.r * np.cos(theta)) * np.sin(phi)
        y = self.r * np.sin(theta)
        w = np.ones_like(z)
        torus = np.array([x, y, z, w]).T
        return np.einsum('...ij,...j', self.pos4d, torus)

    def normal(self, xyz, origin=np.array([-1., 0, 0])):
        '''Return the gradient vector field.

        Following the usual concentions, the vector is pointing outwards
        of the torus volume.

        Parameters
        ----------
        xyz : np.array of shape (N, 3) or (3)
            Coordinates of points in euklidean space. The quartic is calculated for
            those points. All points need to be on the surface of the torus.
        origin : np.array or string
            For a torus with ``r=R`` the normal at the center is ambiguous because it
            is touched by all Rowland circles. When designing an X-ray telescope,
            one usually considers the Rowland circle that points towards the optical
            axis at this point. ``origin`` sets the normal to be returned for this point.
            If ``origin="raise"`` it will raise an error instead if asked to
            calculate an ambiguous normal.
            This parameter has no effect to tori with ``r != R``.

        Returns
        -------
        gradient : np.array
            Gradient vector field in euklidean coordinates. One vector corresponds to each
            input point. The shape of ``gradient`` is the same as the shape of ``xyz``.
        '''
        # For r,R  >> 1 even marginal differences lead to large
        # numbers on the quartic because of R**4 -> normalize
        invpos4d = np.linalg.inv(self.pos4d)
        xyz = h2e(np.einsum('...ij,...j', invpos4d, e2h(xyz, 1)))

        if not np.allclose(self.quartic(xyz, transform=False) / self.R**4., 0.):
            raise ValueError('Gradient vector field is only defined for points on torus surface.')
        factor = 4. * ((xyz**2).sum(axis=-1) + self.R**2. - self.r**2)
        dFdx = factor * xyz[..., 0] - 8. * self.R**2 * xyz[..., 0]
        dFdy = factor * xyz[..., 1]
        dFdz = factor * xyz[..., 2] - 8. * self.R**2 * xyz[..., 2]

        gradient = np.vstack([dFdx, dFdy, dFdz]).T
        index = np.where(np.sum(np.abs(gradient), axis=1) == 0)[0]
        if len(index) > 0:
            if origin == "raise":
                raise ValueError("Ambiguous normal at {0}".format(xyz[index, :]))
            elif len(origin) == 3:
                for i in index:
                    gradient[i, :] = origin
            else:
                raise ValueError("'origin' must be 'raise' or Eukledian vector.")

        gradient = gradient / np.linalg.norm(gradient, axis=1)[:, None]
        return h2e(np.einsum('...ij,...j', self.pos4d, e2h(gradient, 0)))

    def xyz_from_radiusangle(self, radius, angle, interval):
        '''Get Cartesian coordiantes for radius, angle on the rowland circle.

        y, z are calculated from the radius and angle of polar coordiantes in a plane;
        then x is determined from the condition that the point lies on the Rowland circle.
        The plane is perpendicual to the optical axis that defines the Rowland circle.

        Parameters
        ----------
        radius, angle : float or np.array of shape (n,)
            Polar coordinates in a plane perpendicular to the optical axis (where the
            optical axis is parallel to the x-axis and goes through the origin of the
            `RowlandTorus`.
            ``angle=0`` conicides with the local y-axis.
        interval : np.array
            [min, max] for the search. The quartic can have up to for solutions because a
            line can intersect a torus in four points and this interval must bracket one and only
            one solution.

        Returns
        -------
        xyz : np.array of shape (n, 3)
            Eukledian coordinates in the global coordinate system.
        '''
        y = radius * np.cos(angle)
        z = radius * np.sin(angle)
        x = self.solve_quartic(y=y,z=z, interval=interval, transform=False)
        xyz = np.vstack([x,y,z, np.ones_like(x)]).T
        return h2e(np.einsum('...ij,...j', self.pos4d, xyz))

    def _plot_mayavi(self, theta, phi, viewer=None, *args, **kwargs):
        from mayavi.mlab import mesh
        if (theta.ndim != 2) or (theta.shape != phi.shape):
            raise ValueError('"theta" and "phi" must have same 2-dim shape.')
        xyz = h2e(self.parametric(theta.flatten(), phi.flatten()))
        x = xyz[:, 0].reshape(theta.shape)
        y = xyz[:, 1].reshape(theta.shape)
        z = xyz[:, 2].reshape(theta.shape)

        # turn into valid color tuple
        self.display['color'] = get_color(self.display)
        m = mesh(x, y, z, figure=viewer, color=self.display['color'])

        # No safety net here like for color converting to a tuple.
        # If the advanced properties are set you are on your own.
        prop = m.module_manager.children[0].actor.property
        for n in prop.trait_names():
            if n in self.display:
                setattr(prop, n, self.display[n])
        return m


def design_tilted_torus(f, alpha, beta):
    '''Design a torus with specifications similar to Heilmann et al. 2010

    A `RowlandTorus` is fully specified with the parameters ``r``, ``R`` and ``pos4d``.
    However, in practice, these numbers might be derived from other values.
    This function calculates the parameters of a RowlandTorus, based on a different
    set of input values.

    Parameters
    ----------
    f : float
        distance between focal point and on-axis grating. Should be as large as
        possible given the limitations of the spacecraft to increase the resolution.
    alpha : float (in radian)
        angle between optical axis and the line (on-axis grating - center of Rowland circle).
        A typical value could be twice the blaze angle.
    beta : float (in radian)
        angle between optical axis and the line (on-axis grating - hinge), where the hinge
        is a point on the Rowland circle. The Rowland Torus will be constructed by rotating
        the Rowland Circle around the axis (focal point - hinge).
        The center of the Rowland Torus will be the point where the line
        (on-axis grating - center of Rowland circle) intersects the line
        (focal point - hinge).

    Returns
    -------
    R : float
        Radius of Rowland torus. ``r`` determines the radius of the Rowland circle,
        ``R`` is then used to rotate that circle around the axis of symmetry of the torus.
    r : float
        Radius of Rowland circle
    pos4d : np.array of shape (4, 4)

    Notes
    -----
    The geometry used here really needs to be explained in a figure.
    However, some notes to explain at least the meaning of the symbols on the code
    are in order:

    - Cat : position of on-axis CAT grating (where the Rowland circle intersects the on-axis beam)
    - H : position of hinge
    - Ct : Center of Rowland Torus
    - F : Focal point on axis (at the origin of the coordinate system)
    - CatH, HF, FCt, etc. : distance between Cat and H, F and Ct, etc.
    - gamma : see sketch.
    '''
    r = f / (2. * np.cos(alpha))
    CatH = r * np.sqrt(2 * (1 + np.cos(2 * (beta - alpha))))
    HF = np.sqrt(f**2 + CatH**2 - 2 * f * CatH * np.cos(beta))
    # If alpha is negative, then everything is "on the other side".
    # The sign of gamma cannot be found through the arccos, so need to set it here
    # with sign(alpha).
    # Another gotcha: np.sign(0) = 0, but we want 1 (or -1)
    gamma = np.arccos(HF / (2 * r)) * (np.sign(alpha) or 1)
    R = f / np.sin(np.pi - alpha - (alpha + gamma)) * np.sin(alpha + gamma) - r
    FCt = f / np.sin(np.pi - alpha - (alpha + gamma)) * np.sin(alpha)
    x_Ct = FCt * np.cos(alpha + gamma)
    z_Ct = 0
    y_Ct = FCt * np.sin(alpha + gamma)
    orientation = transforms3d.axangles.axangle2mat([0, 0, -1], np.pi/2 - alpha - gamma)
    pos4d = transforms3d.affines.compose([x_Ct, y_Ct, z_Ct], orientation, np.ones(3))
    return R, r, pos4d


class ElementPlacementError(Exception):
    pass


class LinearCCDArray(Parallel, OpticalElement):
    '''A 1D collection of element (e.g. CCDs) arranged on a Rowland circle.

    When a `LinearCCDArray` is initialized, it places a number of elements on the
    Rowland circle. These elements could be any optical element, but the most
    common use for this structure is an array of CCDs capturing a spread-out
    grating spectrum like ACIS-S in Chandra.

    After generation, individual positions can be adjusted by hand by
    editing the attributes `elem_pos` or `elem_uncertainty`. See `Parallel` for details.

    After any of the `elem_pos`, `elem_uncertainty` or
    `uncertainty` is changed, `generate_elements` needs to be
    called to regenerate the final CCD positions..

    Parameters
    ----------
    rowland : RowlandTorus
    d_element : float
        Size of the edge of each element, which is assumed to be flat and square.
        (``d_element`` can be larger than the actual size of the optical element to
        accommodate a minimum distance between elements from mounting structures.
    x_range: list of 2 floats
        Minimum and maximum of the x coordinate that is searched for an intersection
        with the torus. A line can intersect a torus in up to four points. ``x_range``
        specififes the range for the numerical search for the intersection point.
    radius : list of 2 floats
        Inner and outer radius as measured in the yz-plane from the center of the
        `LinearCCDArray`. Can be negative to place elements on both sides of the center
        of the `LinearCCDArray`. Elements will be placed ``d_element`` apart; if a
        non-integer number of elements is needed to cover the ``radius``, elements will
        reach beyond the given numbers.
    phi : floats
        Direction of line through the centers of all elements. :math:`\phi=0`
        is on the positive y axis. Angles are given in radian.
    '''

    tangent_to_torus = True
    '''If ``True`` the default orientation (before applying blaze, uncertainties etc.) of facets is
    such that they are tangents to the torus in the center of the facet.
    If ``False`` they are perpendicular to perfectly focussed rays.
    '''

    id_col = 'CCD_ID'

    def __init__(self, rowland, d_element, x_range, radius, phi, **kwargs):
        self.rowland = rowland
        if not (radius[1] > radius[0]):
            raise ValueError('Outer radius must be larger than inner radius.')
        self.radius = radius

        if np.max(np.abs(phi)) > 10:
            raise ValueError('Input angles >> 2 pi. Did you use degrees (radian expected)?')
        self.phi = phi
        self.x_range = x_range
        self.d_element = d_element

        super(LinearCCDArray, self).__init__(**kwargs)

    def max_elements_on_radius(self):
        '''Distribute elements on a radius.

        Returns
        -------
        n : int
            Number of elements needed to cover a given radius segment.
            Elements might reach beyond the radius limits if the difference between
            inner and outer radius is not an integer multiple of the element size.
        '''
        return int(np.ceil((self.radius[1] - self.radius[0]) / self.d_element))

    def distribute_elements_on_radius(self):
        '''Distributes elements as evenly as possible along a radius.

        .. note::
           Unlike `distribute_elements_on_arc`, this function will have elements reaching
           beyond the limits of the radius, if the distance between inner and outer radius
           is not an integer multiple of the element size.

        Returns
        -------
        radii : np.ndarray
            Radii of the element *center* positions.
        '''
        n = self.max_elements_on_radius()
        return np.mean(self.radius) + np.arange(- n / 2 + 0.5, n / 2 + 0.5) * self.d_element


    def calculate_elempos(self):
        '''Calculate ideal element positions based on rowland geometry.

        Returns
        -------
        pos4d : list of arrays
            List of affine transformations that bring an optical element centered
            on the origin of the coordinate system with the active plane in the
            yz-plane to the required facet position on the Rowland torus.
        '''
        pos4d = []
        radii = self.distribute_elements_on_radius()
        # Line along which the detectors are placed
        try:
            line = normalized_vector(self.rowland.xyz_from_radiusangle(radii[1], self.phi, self.x_range) - self.rowland.xyz_from_radiusangle(radii[0], self.phi, self.x_range))
            for r in radii:
                facet_pos = self.rowland.xyz_from_radiusangle(r, self.phi, self.x_range).flatten()
                if self.tangent_to_torus:
                    facet_normal = self.rowland.normal(facet_pos)
                else:
                    facet_normal = facet_pos
                # rotate such that one edge is parallel to the line
                rot_mat = np.zeros((3,3))
                rot_mat[0, :] = facet_normal
                # Get the part of line that's orthogonal to facet_normal
                rot_mat[1, :] = line - rot_mat[0, :] * np.dot(rot_mat[0, :], line)
                rot_mat[2, :] = normalized_vector(np.cross(rot_mat[0, :], rot_mat[1, :]))
                pos4d.append(transforms3d.affines.compose(facet_pos, rot_mat, np.ones(3)))
        except ValueError as e:
            if 'f(a) and f(b) must have different signs' in str(e):
                raise ElementPlacementError('No intersection with Rowland torus in range {0}'.format(self.x_range))
            else:
                # Something else went wrong
                raise e
        return pos4d


class GratingArrayStructure(LinearCCDArray):
    '''A collection of diffraction gratings on the Rowland torus.

    When a ``GratingArrayStructure`` (GAS) is initialized, it places
    elements in the space available on the Rowland circle, most
    commonly, this class is used to place grating facets.

    After generation, individual facet positions can be adjusted by hand by
    editing the attributes `elem_pos` or `elem_uncertainty`. See `Parallel` for details.

    After any of the `elem_pos`, `elem_uncertainty` or
    `uncertainty` is changed, `generate_elements` needs to be
    called to regenerate the facets on the GAS.

    Parameters
    ----------
    rowland : RowlandTorus
    d_element : float
        Size of the edge of a element, which is assumed to be flat and square.
        (``d_element`` can be larger than the actual size of the silicon membrane to
        accommodate a minimum thickness of the surrounding frame.)
    x_range: list of 2 floats
        Minimum and maximum of the x coordinate that is searched for an intersection
        with the torus. A ray can intersect a torus in up to four points. ``x_range``
        specififes the range for the numerical search for the intersection point.
    radius : list of 2 floats
        Inner and outer radius of the GAS as measured in the yz-plane from the
        origin.
    phi : list of 2 floats
        Bounding angles for a segment covered by the GSA. :math:`\phi=0`
        is on the positive y axis. The segment fills the space from ``phi1`` to
        ``phi2`` in the usual mathematical way (counterclockwise).
        Angles are given in radian. Note that ``phi[1] < phi[0]`` is possible if
        the segment crosses the y axis.

    Notes
    -----
    This class derives from `LinearCCDArray`, which is a 1D arrangement of elements.
    `GratingArrayStructure` also picks radii, but places several elements at
    each radius.
    '''

    tangent_to_torus = False
    '''If ``True`` the default orientation (before applying blaze, uncertainties etc.) of elements is
    such that they are tangents to the torus in the center of the element.
    If ``False`` they are perpendicular to perfectly focussed rays.
    '''

    id_col = 'facet'

    def __init__(self, rowland, d_element, x_range, radius, phi=[0., 2*np.pi], **kwargs):
        if np.min(radius) < 0:
            raise ValueError('Radius must be positive.')

        super(GratingArrayStructure, self).__init__(rowland, d_element, x_range, radius, phi, **kwargs)

    def calc_ideal_center(self):
        '''Position of the center of the GSA, assuming placement on the Rowland circle.'''
        a = (self.phi[0] + anglediff(self.phi) / 2 ) % (2. * np.pi)
        r = sum(self.radius) / 2
        return self.rowland.xyz_from_radiusangle(r, a, self.x_range).flatten()

    def max_elements_on_arc(self, radius):
        '''Calculate maximal number of elements that can be placed at a certain radius.

        Parameters
        ----------
        radius : float
            Radius of circle where the centers of all elements will be placed.
        '''
        return radius * anglediff(self.phi) // self.d_element

    def distribute_elements_on_arc(self, radius):
        '''Distribute elements on an arc.

        The elements are distributed as evenly as possible over the arc.

        .. note::

          Contrary to `distribute_elements_on_radius`, elements never stretch beyond the limits set by the ``phi`` parameter
          of the GAS. If an arc segment is not wide enough to accommodate at least a single element,
          it will go empty.

        Parameters
        ----------
        radius : float
            radius of arc where the elements are to be distributed.

        Returns
        -------
        centerangles : array
            The phi angles for centers of the elements at ``radius``.
        '''
        # arc is most crowded on inner radius
        n = self.max_elements_on_arc(radius - self.d_element / 2)
        element_angle = self.d_element / (2. * np.pi * radius)
        # thickness of space between elements, distributed equally
        d_between = (anglediff(self.phi) - n * element_angle) / (n + 1)
        centerangles = d_between + 0.5 * element_angle + np.arange(n) * (d_between + element_angle)
        return (self.phi[0] + centerangles) % (2. * np.pi)

    def calculate_elempos(self):
        '''Calculate ideal element positions based on rowland geometry.

        Returns
        -------
        pos4d : list of arrays
            List of affine transformations that bring an optical element centered
            on the origin of the coordinate system with the active plane in the
            yz-plane to the required element position on the Rowland torus.
        '''
        pos4d = []
        radii = self.distribute_elements_on_radius()
        for r in radii:
            angles = self.distribute_elements_on_arc(r)
            for a in angles:
                element_pos = self.rowland.xyz_from_radiusangle(r, a, self.x_range).flatten()
                if self.tangent_to_torus:
                    element_normal = np.array(self.rowland.normal(element_pos))
                else:
                    element_normal = element_pos
                # Find the rotation between [1, 0, 0] and the new normal
                # Keep grooves (along e_y) parallel to e_y
                rot_mat = ex2vec_fix(element_normal, np.array([0., 1., 0.]))

                pos4d.append(transforms3d.affines.compose(element_pos, rot_mat, np.ones(3)))
        return pos4d
