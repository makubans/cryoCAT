import numpy as np
from cryocat import cryomap
from skimage import filters
from scipy import ndimage
from skimage import measure
import pandas as pd
import decimal
from skimage import morphology


def add_gaussian(input_mask, sigma):
    """Apply Gaussian filter to the input mask.

    Parameters
    ----------
    input_mask : numpy.ndarray
        3D array with the input mask to be filtered.
    sigma : float
        The standard deviation of the Gaussian filter. If sigma is 0, the input mask is returned as is.

    Returns
    -------
    numpy.ndarray
        3D array with the filtered mask if sigma is not 0, otherwise the input mask itself.

    """

    if sigma == 0:
        return input_mask
    else:
        return filters.gaussian(input_mask, sigma=sigma)


def write_out(input_mask, output_name):
    """Writes out the input mask to a file.

    Parameters
    ----------
    input_mask : numpy.ndarray
        3D array with the input mask to be written out.
    output_name : str
        The name of the output file.

    Returns
    -------
    None

    Examples
    --------
    >>> write_out(input_mask, "output_mask.mrc")
    >>> write_out(input_mask, "output_mask.em")
    """

    if output_name is not None:
        cryomap.write(input_mask, output_name, data_type=np.single)


def rotate(input_mask, angles):
    """Rotates the input mask by the specified angles. The angles are specified in degrees and their order follows
    zxz Euler convention.

    Parameters
    ----------
    input_mask : numpy.ndarray
        3D array with the input mask to be rotated.
    angles : numpy.ndarray
        The angles (in degrees) by which the mask should be rotated. The angles should follow zxz Euler convention.

    Returns
    -------
    numpy.ndarray
        3D array with the rotated mask. If all angles are zero, the input mask is returned as is.

    """

    if angles is None or not np.any(angles):
        return input_mask
    else:
        return cryomap.rotate(input_mask, rotation_angles=angles)


def postprocess(input_mask, gaussian, angles, output_name):
    """Applies set of postprocessing steps to the input_mask. It can smooth the mask by applying Gaussian blur, it
    can rotate it by Euler angles defined in degrees in zxz convention and write it out if output path is specified.

    Parameters
    ----------
    input_mask : numpy.ndarray
        3D array with the mask to postprocess.
    gaussian : float
        Sigma value of the Gaussian blur to be added. If set to 0.0, the Gaussian is not applied.
    angles : numpy.ndarray
        Euler angles in degrees in zxz convention. If all angles are 0.0, the mask is not rotated.
    output_name : str
        Name the output file to write the mask into. If None, the mask will not be written out.

    Returns
    -------
    numpy.ndarray
        3D array with the post-processed mask.

    Notes
    -----
    This function is meant to be used mostly internally to apply a set of post-processing routines.

    """

    mask = add_gaussian(input_mask, gaussian)
    mask = rotate(mask, angles)
    write_out(mask, output_name)

    return mask


def union(mask_list, output_name=None):
    """Calculate the union of multiple masks. The final values are clipped to 0.0 and 1.0.

    Parameters
    ----------
    mask_list : list
        A list of masks (loaded or specified by their paths, or combination of both).
    output_name : str, optional
        The name of the output file. If provided, the final mask is written out. Defaults to None.

    Returns
    -------
    numpy.ndarray
        3D array with the final mask.

    """

    final_mask = np.zeros(cryomap.read(mask_list[0]).shape)

    for m in mask_list:
        mask = cryomap.read(m)
        final_mask += mask

    final_mask = np.clip(final_mask, 0.0, 1.0)

    write_out(final_mask, output_name)

    return final_mask


def intersection(mask_list, output_name=None):
    """Calculate the intersection of multiple masks. The final values are clipped to 0.0 and 1.0.

    Parameters
    ----------
    mask_list : list
        A list of masks (loaded or specified by their paths, or combination of both).
    output_name : str, optional
        The name of the output file. If provided, the final mask is written out. Defaults to None.

    Returns
    -------
    numpy.ndarray
        3D array with the final mask as a numpy array.

    """
    final_mask = np.ones(cryomap.read(mask_list[0]).shape)

    for m in mask_list:
        mask = cryomap.read(m)
        final_mask *= mask

    final_mask = np.clip(final_mask, 0.0, 1.0)
    write_out(final_mask, output_name)

    return final_mask


def subtraction(mask_list, output_name=None):
    """Calculate the subtraction of multiple masks. The subtraction follows the
    order in the list, i.e., the second mask is subtracted from the first one, the third one from the result of the
    first subtraction etc. The final values are clipped to 0.0 and 1.0.

    Parameters
    ----------
    mask_list : list
        A list of masks (loaded or specified by their paths, or combination of both).
    output_name : str, optional
        The name of the output file. If provided, the final mask is written out. Defaults to None.

    Returns
    -------
    numpy.ndarray
        3D array with the final mask as a numpy array.

    """
    final_mask = cryomap.read(mask_list[0]).shape

    for m in mask_list[1:]:
        mask = cryomap.read(m)
        final_mask -= mask

    final_mask = np.clip(final_mask, 0.0, 1.0)
    write_out(final_mask, output_name)

    return final_mask


def difference(mask_list, output_name=None):
    """Calculate the difference between multiple masks. The function first compute the union of all the masks in the
    list and then their intersection which is then substracted from the union. The final values are clipped
    to 0.0 and 1.0.

    Parameters
    ----------
    mask_list : list
        A list of masks (loaded or specified by their paths, or combination of both).
    output_name : str, optional
        The name of the output file. If provided, the final mask is written out. Defaults to None.

    Returns
    -------
    numpy.ndarray
        3D array with the final mask after calculating the difference.

    Examples
    --------
        difference([mask1, 'mask2.em', 'mask3.mrc'], output_name='output.mrc')

    """

    union_mask = union(mask_list)
    inter_mask = intersection(mask_list)

    final_mask = union_mask - inter_mask
    final_mask = np.clip(final_mask, 0.0, 1.0)
    write_out(final_mask, output_name)

    return final_mask


def spherical_mask(mask_size, radius=None, center=None, gaussian=0.0, gaussian_outwards=True, output_name=None):
    """Creates a spherical mask with the specified radius, center and box size. The values range from 0.0 to 1.0.
    Additionally, the mask can be blurred by applying Gaussian specified by its sigma value.

    Parameters
    ----------
    mask_size : array-like
        Specifies the dimensions of the box for the mask. Type is `int`.
    radius : int, optional
        Defines the radius of the sphere in voxels. If not specified half of the smallest
        dimensions is used as the radius. Defaults to None.
    center : array-like, optional
        Specify the center of the mask within the box. If not specified, the mask is placed in the center of the box
        (e.g. for box size of 64 the center will be at (32, 32, 32) when numbered from 0). Type `int`. Defaults to None.
    gaussian : float, default=0.0
        Defines the sigma of the Gaussian blur. If set to 0 no blur is applied. Defaults to 0.
    gaussian_outwards : bool, default=True
        Determines if the blur will be done outwards from the sphere surface (True) or if it will be centered around
        the sphere surface (False). The latter is consistent with Dynamo convention. Defaults to True.
    output_name : str, optional
        Path to write out the created mask. If not specified, the mask is not written out.
        Defaults to None.

    Returns
    -------
    numpy.ndarray
        3D array with the spherical mask.

    Warning
    -------
    In the previous version, the default behavior was to smooth the mask centrally around its surface. To achive the
    same effect one has to set gaussian_outwards to False (default is True).

    See Also
    --------
    :meth:`cryocat.cryomask.get_correct_format` :
        For more information on formatting the inputs.

    """

    mask_size = get_correct_format(mask_size)
    center = get_correct_format(center, reference_size=mask_size)

    if radius is None:
        radius = np.amin(mask_size) // 2

    radius = preprocess_params(radius, gaussian, gaussian_outwards)

    x, y, z = np.mgrid[0 : mask_size[0] : 1, 0 : mask_size[1] : 1, 0 : mask_size[2] : 1]
    mask = np.sqrt((x - center[0]) ** 2 + (y - center[1]) ** 2 + (z - center[2]) ** 2)
    mask[mask > radius] = 0
    mask[mask > 0] = 1
    mask[center[0], center[1], center[2]] = 1

    mask = postprocess(mask, gaussian, np.asarray([0, 0, 0]), output_name)

    return mask


def cylindrical_mask(
    mask_size,
    radius=None,
    height=None,
    center=None,
    gaussian=0,
    gaussian_outwards=True,
    angles=None,
    output_name=None,
):
    """Creates a cylindrical mask with the specified radius, height, center and box size. The values range from 0.0 to 1.0.
    Additionally, the mask can be blurred by applying Gaussian specified by its sigma value and/or
    rotated by specifying Euler angles in degrees in zxz convention.

    Parameters
    ----------
    mask_size : array-like
        Specifies the dimensions of the box for the mask. Type is `int`.
    radius : int, optional
        Defines the radius of the cylinder base in voxels. If not specified half of the smallest
        dimensions in (x,y) is used as the radius. Defaults to None.
    height : int, optional
        Defines the height of the cylinder in voxels. If not specified the dimension z
        is used as the height. Defaults to None.
    center : array-like, optional
        Specify the center of the mask within the box. If not specified, the mask is placed in the center of the box
        (e.g. for box size of 64 the center will be at (32, 32, 32) when numbered from 0). Type is `int`. Defaults to None.
    gaussian : float, default=0.0
        Defines the sigma of the Gaussian blur. If set to 0 no blur is applied. Defaults to 0.
    gaussian_outwards : bool, default=True
        Determines if the blur will be done outwards from the mask surface (True) or if it will be centered around the
        mask surface (False). The latter is consistent with Dynamo convention. Defaults to True.
    angles : numpy.ndarray, optional
        1D array defining the rotation of the mask specified as three Euler angles in degrees in
        zxz convention. If all angles are zero, no rotation is applied. Defaults to None.
    output_name : str, optional
        Path to write out the created mask. If not specified, the mask is not written out.
        Defaults to None.


    Returns
    -------
    numpy.ndarray
        3D array containing the cylindrical mask.

    Warnings
    --------
        In the previous version, the default behavior was to smooth the mask centrally around its surface. To achive the
        same effect one has to set gaussian_outwards to False (default is True).

    See Also
    --------
    :meth:`cryocat.cryomask.get_correct_format` :
        For more information on formatting the inputs.

    """
    mask_size = get_correct_format(mask_size)
    center = get_correct_format(center, reference_size=mask_size)

    if radius is None:
        radius = np.amin(mask_size[:2]) // 2  # only x, y are relevant

    if height is None:
        height = mask_size[2]

    height = height // 2

    radius = preprocess_params(radius, gaussian, gaussian_outwards)
    height = preprocess_params(height, gaussian, gaussian_outwards)

    x, y = np.mgrid[0 : mask_size[0] : 1, 0 : mask_size[1] : 1]
    mask_xy = np.sqrt((x - center[0]) ** 2 + (y - center[1]) ** 2)
    mask_xy[mask_xy > radius] = 0
    mask_xy[mask_xy > 0] = 1
    mask_xy[center[0], center[1]] = 1

    mask = np.zeros(mask_size)
    mask[:, :, center[2] - height : center[2] + height + 1] = np.tile(mask_xy[:, :, None], (1, 1, height * 2 + 1))

    mask = postprocess(mask, gaussian, angles, output_name)

    return mask


def get_correct_format(input_value, reference_size=None):
    """Formats correctly the size of the mask, radius or coordinates of the mask centers.
    If the input size is specified, it will be converted to a `numpy.ndarray` of length 3.
    If the reference size is specified, it will be converted to a `numpy.ndarray` of length 3 and then divided by 2.

    Parameters
    ----------
    input_value : int
        Specify the value either by one number, tuple, list or numpy.ndarray.
        In case of one number, it is assumed that the output should be the same for all three dimensions.
    reference_size : int, optional
        If input_value is None, then the reference size can be used to compute the correct output. For example,
        getting the center coordinates based on the box size (reference_size) by dividing the box size by 2. Defaults
        to None.

    Returns
    -------
    numpy.ndarray
        An (3,) array with size/coordinates.

    Raises
    ------
    ValueError
        If the size is specified as a container with size different from 1 or 3.
    ValueError
        If both input_size and reference_size are None.

    Notes
    -----
    This function is mainly use internally to ensure correct format of the input values, such as mask and radius
    size or center coordinates.

    """

    def format_input(unformatted_value):
        if isinstance(unformatted_value, (tuple, list, np.ndarray)):
            if len(unformatted_value) == 3:
                return np.asarray(unformatted_value).astype(int)
            elif len(unformatted_value) == 1:
                return np.full((3,), unformatted_value).astype(int)
            else:
                raise ValueError("The size have to be a single number or have to have length of 3!")
        elif isinstance(unformatted_value, (float, int)):
            return np.full((3,), unformatted_value).astype(int)

    if input_value is not None:
        size_correct_format = format_input(input_value)
    elif reference_size is not None:
        box_size = format_input(reference_size)
        size_correct_format = box_size // 2
    else:
        raise ValueError("Either input_size or referene_size have to be specified")

    return size_correct_format


def ellipsoid_mask(
    mask_size,
    radii=None,
    center=None,
    gaussian=0,
    output_name=None,
    angles=None,
    gaussian_outwards=True,
):
    """Creates an ellipsoid mask with the specified radii (for x, y, z), center and box size. The values range from
    0.0 to 1.0. Additionally, the mask can be blurred by applying Gaussian specified by its sigma value and/or
    rotated by specifying Euler angles in degrees in zxz convention.

    Parameters
    ----------
    mask_size : array-like
        Specifies the dimensions of the box for the mask.
    radii : array-like, optional
        Defines the radii of the ellipsoid in voxels. If not specified half of the dimensions of the box size are
        used as the radii. Defaults to None.
    center : array-like, optional
        Specify the center of the mask within the box. If not specified, the mask is placed in the center of the box
        (e.g. for box size of 64 the center will be at (32, 32, 32) when numbered from 0). Defaults to None.
    gaussian : float, default=0.0
        Defines the sigma of the Gaussian blur. If set to 0 no blur is applied. Defaults to 0.
    gaussian_outwards : bool, default=True
        Determines if the blur will be done outwards from the mask surface (True) or if it will be centered around the
        mask surface (False). The latter is consistent with Dynamo convention. Defaults to True.
    angles : numpy.ndarray, optional
        1D array defining the rotation of the mask specified as three Euler angles in degrees in
        zxz convention. If all angles are zero, no rotation is applied. Defaults to None.
    output_name : str, optional
        Path to write out the created mask. If not specified, the mask is not written out.
        Defaults to None.

    Returns
    -------
    numpy.ndarray
        3D array containing the ellipsoid mask.

    Warnings
    --------
        In the previous version, the default behavior was to smooth the mask centrally around its surface. To achive the
        same effect one has to set gaussian_outwards to False (default is True).

    See Also
    --------
    :meth:`cryocat.cryomask.get_correct_format` :
        For more information on formatting the inputs.

    """
    mask_shape = get_correct_format(mask_size)
    center = get_correct_format(center, reference_size=mask_shape)
    radii = get_correct_format(radii, reference_size=mask_shape)

    radii = preprocess_params(radii, gaussian, gaussian_outwards)

    # Build a grid and get its points as a list
    xi = tuple(np.linspace(1, s, s) - np.floor(0.5 * s) for s in mask_shape)

    # Build a list of points forming the grid
    xi = np.meshgrid(*xi, indexing="ij")
    points = np.array(xi).reshape(3, -1)[::-1]

    # Find grid center
    grid_center = 0.5 * mask_shape - center
    grid_center = np.tile(grid_center.reshape(3, 1), (1, points.shape[1]))

    # Reorder coordinates back to ZYX to match the order of numpy array axis
    points = points[:, ::-1]
    grid_center = grid_center[::-1]
    radii = radii[::-1]
    radii = np.tile(radii.reshape(3, 1), (1, points.shape[1]))

    # Draw the ellipsoid
    # dx**2 + dy**2 + dz**2 = r**2
    # dx**2 / r**2 + dy**2 / r**2 + dz**2 / r**2 = 1
    ellipsoid = (points - grid_center) ** 2
    ellipsoid = ellipsoid / radii**2
    # Sum dx, dy, dz / r**2
    distance = np.sum(ellipsoid, axis=0).reshape(mask_shape)

    mask = distance <= 1

    mask = postprocess(mask, gaussian, angles, output_name)

    return mask


def preprocess_params(radius, gaussian, gaussian_outwards):
    """Determines a new radius or dilation in case of Gaussian blur that should be applied outwards from the mask
    surface. If the Gaussian is 0 or the blur should be applied centrally around the mask surface, the radius/dilation
    remains unchanged. Otherwise, it new radius/dilation is computed as radius + gaussian * blur_factor. The blur_factor
    was determined empirically and set to 5 (i.e., Gaussian with value 1 requires extension of radius by 5 voxels in
    order not to affect the core part of the mask by the blur).

    Parameters
    ----------
    radius : int
        Defines radius to be recomputed in case Gaussian blur is not zero and gaussian_outwards is True.
    gaussian : float
        Defines the sigma of the Gaussian blur.
    gaussian_outwards : bool
        Defines whether the Gaussian blur should be applied outwards form the surface (True) or
        centrally around the mask surface (False).

    Returns
    -------
    int
        The radius/dilation adapted based on the gaussian and gaussian_outwards parameters.

    Notes
    -----
        This function is meant to be used mostly internally to adapt the radius/dilation based on the Gaussian blur.

    """

    blur_factor = 5.0

    if gaussian != 0.0 and gaussian_outwards:
        new_radius = np.ceil(radius + gaussian * blur_factor).astype(int)
    else:
        new_radius = radius

    return new_radius


def molmap_tight_mask(
    input_map,
    threshold=0.0,
    dilation_size=0,
    gaussian=0,
    gaussian_outwards=True,
    angles=None,
    output_name=None,
):
    """Creates a tight mask for the density created with molmap function in Chimera(X). The mask is the same shape as
    the input map, but has values from 0.0 to 1.0. Additionally, the mask can be blurred by applying Gaussian specified
    by its sigma value and/or rotated by specifying Euler angles in degrees in zxz convention.

    Parameters
    ----------
    input_map : str or numpy.ndarray
        Input molmap specified either by its path or already loaded as 3D numpy.ndarray.
    threshold : float, default=0.0
        Values from the input map larger than this threshold will be included in the mask.
        This value is used inly if dilation_size is 0. Defaults to 0.0.
    dilation_size : int, default=0
        Determines the number of iterations for the dilation operation. If gaussian_outwards
        is True, the dilation_size can be 0 unless larger mask extension is wanted. Defaults to 0.
    gaussian : float, default=0.0
        Defines the sigma of the Gaussian blur. If set to 0 no blur is applied. Defaults to 0.
    gaussian_outwards : bool, default=True
        Determines if the blur will be done outwards from the mask surface (True)
        or if it will be centered around the mask surface (False). Defaults to True.
    angles : numpy.ndarray, optional
        Defines the rotation of the mask specified as three Euler angles in degrees in
        zxz convention. If all angles are zero, no rotation is applied. Defaults to None.
    output_name : str, optional
        Path to write out the created mask. If not specified, the mask is not written out. Defaults to None.

    Returns
    -------
    numpy.array
        A tight mask for the provided molmap density.

    Warnings
    --------
    In the previous version, the default behavior was to smooth the mask centrally around its surface. To achive the
    same effect one has to set gaussian_outwards to False (default is True).

    """

    model = cryomap.read(input_map)

    dilation_size = preprocess_params(dilation_size, gaussian, gaussian_outwards)

    if dilation_size == 0:
        mask = np.where(model > threshold, 1.0, 0.0)
    else:
        mask = ndimage.binary_dilation(model, iterations=dilation_size)

    mask = postprocess(mask, gaussian, angles, output_name)

    return mask


def map_tight_mask(
    input_map,
    threshold=None,
    dilation_size=0,
    gaussian=0,
    gaussian_outwards=True,
    angles=None,
    n_regions=1,
    output_name=None,
):
    """Creates a tight mask for the map coming from STA/SPA (i.e., with some noise around it). The mask is the same shape as
    the input map at given threshold. It has values from 0.0 to 1.0. Additionally, the mask can be blurred by
    applying Gaussian specified by its sigma value and/or rotated by specifying Euler angles in degrees in zxz convention.

    Parameters
    ----------
    input_map : str or numpy.ndarray
        Input map specified either by its path or already loaded as 3D numpy.ndarray.
    threshold : float, optional
        In case the threshold is negative, the values below this threshold will be included in the mask. In case of
        positive threshold, the values from the input map larger than this threshold will be included in the mask.
        If the value is None, the threshold is determined as 3 * standard deviation of the input map (or its negative
        value in case the median is larger than 0.0 which correspond to the densities being dark). Defaults to None.
    dilation_size : int, default=0
        Determines the number of iterations for the dilation operation. If gaussian_outwards is True, the dilation_size
        can be 0 unless larger mask extension is wanted. Defaults to 0.
    gaussian : float, default=0.0
        Defines the sigma of the Gaussian blur. If set to 0 no blur is applied. Defaults to 0.
    gaussian_outwards : bool, default=True
        Determines if the blur will be done outwards from the mask surface (True)
        or if it will be centered around the mask surface (False). Defaults to True.
    angles : numpy.ndarray, optional
        Defines the rotation of the mask specified as three Euler angles in degrees in
        zxz convention. If all angles are zero, no rotation is applied. Defaults to None.
    n_regions : int, default=1
        Determines how many connected regions should be part of the mask. After the input map is thresholded, the
        connected regions are labeled and "n" largerst regions (in terms of number of voxels) are returned as the mask.
        Defaults to 1.
    output_name : str, optional
        Path to write out the created mask. If not specified, the mask is not written out. Defaults to None.

    Returns
    -------
    numpy.ndarray
        3D array with the tight mask for the provided map density.

    Warnings
    --------
    In the previous version, the default behavior was to smooth the mask centrally around its surface. To achive the
    same effect one has to set gaussian_outwards to False (default is True).

    """

    mask = cryomap.read(input_map)

    if threshold is None:
        threshold = 3.0 * np.std(mask)
        if np.median(mask) > 0.0:
            threshold *= -1

    if threshold < 0.0:
        mask = np.where(mask < threshold, 1.0, 0.0)
    else:
        mask = np.where(mask > threshold, 1.0, 0.0)

    labeled_mask = measure.label(mask, connectivity=1)
    info_table = pd.DataFrame(
        measure.regionprops_table(
            labeled_mask,
            properties=["label", "area"],
        )
    ).set_index("label")
    info_table = info_table.reset_index()

    label_ids = info_table.sort_values(by="area", ascending=False).head(n_regions)["label"].values
    # label_id = info_table.iloc[info_table['area'].idxmax()]['label']
    mask = np.where(np.isin(labeled_mask, [label_ids]), 1.0, 0.0)

    dilation_size = preprocess_params(dilation_size, gaussian, gaussian_outwards)

    if dilation_size > 0:
        mask = ndimage.binary_dilation(mask, iterations=dilation_size)

    mask = postprocess(mask, gaussian, angles, output_name)

    return mask


def get_bounding_box(input_mask):
    """Get the bounding box indices of a given input mask.

    Parameters
    ----------
    input_mask : str or numpy.ndarray
        Input mask specified either by its path or already loaded as 3D numpy.ndarray.

    Returns
    -------
    start_ids : numpy.ndarray
        1D array with the starting coordinates of the bounding box with shape (3,) and type `int`.
    end_ids : numpy.ndarray
        1D array with the starting coordinates of the bounding box with shape (3,) and type `int`.

    """

    mask = cryomap.read(input_mask)
    epsilon = 0.00001
    i, j, k = np.asarray(mask > epsilon).nonzero()
    if i.shape[0] == 0:
        return np.zeros((3,)).astype(int), np.zeros((3,)).astype(int)
    start_ids = np.array([min(i), min(j), min(k)])
    end_ids = np.array([max(i), max(j), max(k)])

    return start_ids, end_ids


def get_mass_dimensions(input_mask):
    """Get the dimensions of the mass in the input mask.

    Parameters
    ----------
    input_mask : str or numpy.ndarray
        Input mask specified either by its path or already loaded as 3D numpy.ndarray.

    Returns
    -------
    numpy.ndarray
        The dimensions of the mass in the format [width, height, depth].

    """

    mask = cryomap.read(input_mask)

    start_ids, end_ids = get_bounding_box(mask)

    return end_ids - start_ids + 1


def get_mass_center(input_mask):
    """Calculate the mass center of a given input mask.

    Parameters
    ----------
    input_mask : str or numpy.ndarray
        Input mask specified either by its path or already loaded as 3D numpy.ndarray.

    Returns
    -------
    numpy.ndarray
        The mass center coordinates as an array of integers with shape (3,).

    """

    mask = cryomap.read(input_mask)
    start_ids, end_ids = get_bounding_box(mask)

    mask_center = (start_ids + end_ids) / 2

    for i in range(3):
        mask_center[i] = decimal.Decimal(mask_center[i]).to_integral_value(rounding=decimal.ROUND_HALF_UP) + 1

    return mask_center.astype(int)


def shrink_full_mask(input_mask, shrink_factor, output_name=None):
    """Takes in a 3D binary mask and shrinks it by the specified shrink factor. The function first fills in all of
    the holes within each slice of the mask, then shrinks it by removing the outermost layer of voxels from each slice.
    The function returns a new shrunken binary mask.

    Parameters
    ----------
    input_mask : str or numpy.ndarray
        Input mask specified either by its path or already loaded as 3D numpy.ndarray.
    shrink_factor : int
        Defines how much the mask should be shrunken (in voxels).
    output_name : str, optional
        Path to write out the created mask. If not specified, the mask is not written out. Defaults to None.


    Returns
    -------
    numpy.ndarray
        3D array with the mask shrunken by the specified factor.

    """

    input_mask = cryomap.read(input_mask)

    dim_x, dim_y, dim_z = input_mask.shape
    filled_mask = np.zeros(input_mask.shape)

    for z in range(dim_z):
        for y in range(dim_y):
            not_zero = np.flatnonzero(input_mask[:, y, z])
            if not_zero.size != 0:
                s_idx = not_zero[0] + shrink_factor
                e_idx = not_zero[-1] + 1 - shrink_factor
                if e_idx - s_idx > 0:
                    filled_mask[s_idx:e_idx, y, z] = 1

    for z in range(dim_z):
        for x in range(dim_x):
            not_zero = np.flatnonzero(filled_mask[x, :, z])
            if not_zero.size != 0:
                s_idx = not_zero[0] + shrink_factor
                e_idx = not_zero[-1] + 1 - shrink_factor
                if e_idx - s_idx > 0:
                    filled_mask[x, s_idx:e_idx, z] += 1

    for y in range(dim_y):
        for x in range(dim_x):
            not_zero = np.flatnonzero(filled_mask[x, y, :])
            if not_zero.size != 0:
                s_idx = not_zero[0] + shrink_factor
                e_idx = not_zero[-1] + 1 - shrink_factor
                if e_idx - s_idx > 0:
                    filled_mask[x, y, s_idx:e_idx] += 1

    filled_mask = np.where(filled_mask == 3, 1, 0)

    filled_mask = morphology.binary_opening(filled_mask, footprint=np.ones((2, 2, 2)))
    filled_mask = morphology.binary_closing(filled_mask)

    write_out(filled_mask, output_name)

    return filled_mask


def fill_hollow_mask(input_mask, output_name=None):
    """Takes in a binary mask and returns the same mask with all holes filled in.

    Parameters
    ----------
    input_mask : str or numpy.ndarray
        Input mask specified either by its path or already loaded as 3D numpy.ndarray.
    output_name : str, optional
        Path to write out the created mask. If not specified, the mask is not written out. Defaults to None.

    Returns
    -------
    numpy.ndarray
        3D array with the input mask with the holes filled in.

    """

    input_mask = cryomap.read(input_mask)

    dim_x, dim_y, dim_z = input_mask.shape
    filled_mask = np.zeros(input_mask.shape)

    for z in range(dim_z):
        for y in range(dim_y):
            not_zero = np.flatnonzero(input_mask[:, y, z])
            if not_zero.size != 0:
                filled_mask[not_zero[0] : not_zero[-1] + 1, y, z] = 1

    for z in range(dim_z):
        for x in range(dim_x):
            not_zero = np.flatnonzero(filled_mask[x, :, z])
            if not_zero.size != 0:
                filled_mask[x, not_zero[0] : not_zero[-1] + 1, z] += 1

    for y in range(dim_y):
        for x in range(dim_x):
            not_zero = np.flatnonzero(filled_mask[x, y, :])
            if not_zero.size != 0:
                filled_mask[x, y, not_zero[0] : not_zero[-1] + 1] += 1

    filled_mask = np.where(filled_mask > 0, 1, 0)

    filled_mask = morphology.binary_opening(filled_mask, footprint=np.ones((2, 2, 2)))
    filled_mask = morphology.binary_closing(filled_mask)

    write_out(filled_mask, output_name)

    return filled_mask


def compute_solidity(input_mask):
    """Computes the solidity of a given input mask.

    Parameters
    ----------
    input_mask : numpy.ndarray
        The input mask to compute the solidity for specified as 3D array.

    Returns
    -------
    float
        The solidity value of the input mask.

    """

    mask_label = measure.label(input_mask)
    props = pd.DataFrame(measure.regionprops_table(mask_label, properties=["solidity"]))

    return props.at[0, "solidity"]


def mask_overlap(mask1, mask2, threshold=1.9):
    """Calculate the overlap between two masks.

    Parameters
    ----------
    mask1 : numpy.ndarray
        The first mask as 3D array.
    mask2 : numpy.ndarray
        The second mask as 3D array.
    threshold : float
        The threshold value for determining the overlap. Defaults to 1.9.

    Returns
    -------
    int
        The sum of the overlapping voxels between the two masks that have value larger than the threshold.

    """

    mask_overlap = np.where((mask1 + mask2) <= threshold, 0, 1)

    return np.sum(mask_overlap)
