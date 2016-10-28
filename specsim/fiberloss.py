# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""Calculate fiberloss fractions.

Fiberloss fractions are computed as the overlap between the light profile
illuminating a fiber and the on-sky aperture of the fiber.
"""
from __future__ import print_function, division

import numpy as np

import astropy.units as u
import astropy.io.fits
import astropy.wcs


def calculate_fiber_acceptance_fraction(
    focal_x, focal_y, wavelength, source, atmosphere, instrument, save=None):
    """
    """
    # Use pre-tabulated fiberloss vs wavelength when available.
    if instrument.fiberloss_ngrid == 0:
        return instrument.fiber_acceptance_dict[source.type_name]

    # Galsim is required to calculate fiberloss fractions on the fly.
    import galsim

    wlen_unit = wavelength.unit
    wlen_grid = np.linspace(wavelength.data[0], wavelength.data[-1],
                            instrument.fiberloss_ngrid) * wlen_unit

    # Calculate the field angle from the focal-plane (x,y).
    focal_r = np.sqrt(focal_x ** 2 + focal_y ** 2)
    angle = instrument.field_radius_to_angle(focal_r)

    # Create the instrument blur PSF and lookup the centroid offset at each
    # wavelength for this focal-plane position.
    blur_psf = []
    offsets = []
    for wlen in wlen_grid:
        blur_rms = instrument.get_blur_rms(wlen, angle)
        # Convert to an angular size on the sky ignoring any asymmetry that
        # might be introduced by different radial and azimuthal plate scales.
        rscale = instrument.radial_scale(focal_r)
        blur_rms /= rscale
        blur_psf.append(galsim.Gaussian(sigma=blur_rms.to(u.arcsec).value))
        offset = instrument.get_centroid_offset(wlen, angle)
        # Convert to an angular offset on the sky.
        offset /= rscale
        offsets.append(offset.to(u.arcsec).value)

    # Create the atmospheric seeing model at each wavelength.
    seeing_psf = []
    for wlen in wlen_grid:
        seeing_psf.append(galsim.Moffat(
            fwhm=atmosphere.get_seeing_fwhm(wlen).to(u.arcsec).value,
            beta=atmosphere.seeing['moffat_beta']))

    # Create the source model, which we assume to be achromatic.
    source_components = []
    if source.disk_fraction > 0:
        hlr = source.disk_shape.half_light_radius.to(u.arcsec).value
        q = source.disk_shape.minor_major_axis_ratio
        beta = source.disk_shape.position_angle.to(u.deg).value
        disk_model = galsim.Exponential(
            flux=source.disk_fraction, half_light_radius=hlr).shear(
                q=q, beta=beta * galsim.degrees)
    if source.disk_fraction < 1:
        hlr = source.bulge_shape.half_light_radius.to(u.arcsec).value
        q = source.bulge_shape.minor_major_axis_ratio
        beta = source.bulge_shape.position_angle.to(u.deg).value
        bulge_model = galsim.DeVaucouleurs(
            flux=source.disk_fraction, half_light_radius=hlr).shear(
                q=q, beta=beta * galsim.degrees)
    if source.disk_fraction == 0:
        source_model = bulge_model
    elif source.disk_fraction == 1:
        source_model = disk_model
    else:
        source_model = disk_model + bulge_model

    # Calculate the on-sky fiber aperture.
    radial_size = (instrument.fiber_diameter /
                   instrument.radial_scale(focal_r)).to(u.arcsec).value
    azimuthal_size = (instrument.fiber_diameter /
                      instrument.azimuthal_scale(focal_r)).to(u.arcsec).value

    # Prepare an image of the fiber aperture for numerical integration.
    scale = instrument.fiberloss_pixel_size.to(u.arcsec).value
    npix_r = np.ceil(radial_size / scale)
    npix_phi = np.ceil(azimuthal_size / scale)
    image = galsim.Image(npix_r, npix_phi, scale=scale)

    # Calculate the coordinates at center of each image pixel relative to
    # the fiber center.
    dr = (np.arange(npix_r) - 0.5 * npix_r) * scale
    dphi = (np.arange(npix_phi) - 0.5 * npix_phi) * scale

    # Select pixels whose center is within the fiber aperture.
    inside = (
        (2 * dr / radial_size) ** 2 +
        (2 * dphi[:, np.newaxis] / azimuthal_size) ** 2 <= 1.0)

    # Prepare to write a FITS file of images, if requested.
    if save:
        hdu_list = astropy.io.fits.HDUList()
        header = astropy.io.fits.Header()
        header['COMMENT'] = 'Fiberloss calculation images.'
        hdu_list.append(astropy.io.fits.PrimaryHDU(header=header))
        # All subsequent HDUs contain images with the same WCS.
        w = astropy.wcs.WCS(naxis=2)
        w.wcs.ctype = ['x', 'y']
        w.wcs.crpix = [npix_r / 2. + 0.5, npix_phi / 2. + 0.5]
        w.wcs.cdelt = [scale, scale]
        w.wcs.crval = [0., 0.]
        header = w.to_header()

    # Build the convolved models and integrate.
    gsparams = galsim.GSParams(maximum_fft_size=32767)
    for i, wlen in enumerate(wlen_grid):
        convolved = galsim.Convolve([
            blur_psf[i], seeing_psf[i], source_model], gsparams=gsparams)
        # TODO: test if method='no_pixel' is faster and accurate enough.
        convolved.drawImage(image=image, method='auto',
                            offset=(offsets[i], 0.))
        fraction = np.sum(image.array[inside])
        print('fiberloss:', wlen, offsets[i], fraction)
        if save:
            header['COMMENT'] = '{0:.1f} Convolved model'.format(wlen)
            header['WLEN'] = wlen.to(u.Angstrom).value
            header['FRAC'] = fraction
            hdu_list.append(astropy.io.fits.ImageHDU(
                data=image.array.copy(), header=header))

    if save:
        hdu_list.writeto(save, clobber=True)

    return instrument.fiber_acceptance_dict[source.type_name]
